import asyncio
import logging
import sqlite3
from datetime import datetime, timedelta
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton
import os

# ================== НАСТРОЙКИ ==================
BOT_TOKEN = "8649368544:AAGCWWO6P04pwN9Qlfb3RPKeTcaTM-3Heeo"
ADMIN_IDS = "8649368544:AAHwVgfwejPkfkzO9Z0KNcljGNyaL3Bk6Dc"
REFERRAL_BONUS = 5  # Бонус за реферала
MIN_WITHDRAW = 25  # Минимальная сумма вывода
CHECK_INTERVAL = 3600  # Проверка подписок каждые 3600 секунд (1 час)
# ==============================================

# Создаем папку для скриншотов, если её нет
if not os.path.exists("screenshots"):
    os.makedirs("screenshots")

# Настройка логирования
logging.basicConfig(level=logging.INFO)

# Инициализация бота и диспетчера
bot = Bot(token=BOT_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

# ================== БАЗА ДАННЫХ ==================
class Database:
    def __init__(self):
        self.conn = sqlite3.connect('bot_database.db', check_same_thread=False)
        self.cursor = self.conn.cursor()
        self.create_tables()
    
    def create_tables(self):
        # Таблица пользователей
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                balance REAL DEFAULT 0,
                total_earned REAL DEFAULT 0,
                referrer_id INTEGER,
                referrals_count INTEGER DEFAULT 0,
                joined_date TEXT
            )
        ''')
        
        # Таблица рефералов
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS referrals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                referrer_id INTEGER,
                referral_id INTEGER,
                date TEXT
            )
        ''')
        
        # Таблица обязательных каналов
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS required_channels (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                channel_id TEXT,
                channel_username TEXT,
                channel_title TEXT,
                added_date TEXT
            )
        ''')
        
        # Таблица промокодов
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS promocodes (
                code TEXT PRIMARY KEY,
                amount REAL,
                max_uses INTEGER,
                current_uses INTEGER DEFAULT 0
            )
        ''')
        
        # Таблица использованных промокодов
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS promocode_uses (
                user_id INTEGER,
                code TEXT,
                date TEXT
            )
        ''')
        
        # Таблица заданий
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT,
                description TEXT,
                reward REAL,
                type TEXT,
                target TEXT,
                created_date TEXT
            )
        ''')
        
        # Таблица выполненных заданий
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS completed_tasks (
                user_id INTEGER,
                task_id INTEGER,
                date TEXT,
                PRIMARY KEY (user_id, task_id)
            )
        ''')
        
        # Таблица заявок на вывод
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS withdrawals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                amount REAL,
                wallet TEXT,
                screenshot_path TEXT,
                status TEXT DEFAULT 'pending',
                date TEXT
            )
        ''')
        
        self.conn.commit()
    
    # ===== Управление каналами =====
    def add_channel(self, channel_id, channel_username, channel_title):
        date = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.cursor.execute('''
            INSERT OR IGNORE INTO required_channels (channel_id, channel_username, channel_title, added_date)
            VALUES (?, ?, ?, ?)
        ''', (str(channel_id), channel_username, channel_title, date))
        self.conn.commit()
        return self.cursor.lastrowid
    
    def remove_channel(self, channel_id):
        self.cursor.execute('DELETE FROM required_channels WHERE channel_id = ?', (str(channel_id),))
        self.conn.commit()
    
    def get_channels(self):
        self.cursor.execute('SELECT * FROM required_channels')
        return self.cursor.fetchall()
    
    def channel_exists(self, channel_username):
        self.cursor.execute('SELECT * FROM required_channels WHERE channel_username = ?', (channel_username,))
        return self.cursor.fetchone() is not None
    
    # ===== Пользователи =====
    def add_user(self, user_id, username, first_name, referrer_id=None):
        date = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.cursor.execute('''
            INSERT OR IGNORE INTO users (user_id, username, first_name, referrer_id, joined_date)
            VALUES (?, ?, ?, ?, ?)
        ''', (user_id, username, first_name, referrer_id, date))
        self.conn.commit()
        
        if referrer_id:
            self.cursor.execute('SELECT * FROM referrals WHERE referral_id = ?', (user_id,))
            if not self.cursor.fetchone():
                self.cursor.execute('''
                    UPDATE users SET balance = balance + ?, total_earned = total_earned + ?, referrals_count = referrals_count + 1
                    WHERE user_id = ?
                ''', (REFERRAL_BONUS, REFERRAL_BONUS, referrer_id))
                
                self.cursor.execute('''
                    INSERT INTO referrals (referrer_id, referral_id, date)
                    VALUES (?, ?, ?)
                ''', (referrer_id, user_id, date))
                self.conn.commit()
    
    def get_user(self, user_id):
        self.cursor.execute('SELECT * FROM users WHERE user_id = ?', (user_id,))
        return self.cursor.fetchone()
    
    # ===== Промокоды =====
    def create_promocode(self, code, amount, max_uses):
        self.cursor.execute('''
            INSERT INTO promocodes (code, amount, max_uses)
            VALUES (?, ?, ?)
        ''', (code.upper(), amount, max_uses))
        self.conn.commit()
    
    def use_promocode(self, user_id, code):
        code = code.upper()
        
        self.cursor.execute('SELECT * FROM promocodes WHERE code = ?', (code,))
        promo = self.cursor.fetchone()
        
        if not promo:
            return False, "❌ Промокод не найден"
        
        if promo[3] >= promo[2]:
            return False, "❌ Промокод больше недействителен"
        
        self.cursor.execute('SELECT * FROM promocode_uses WHERE user_id = ? AND code = ?', (user_id, code))
        if self.cursor.fetchone():
            return False, "❌ Вы уже использовали этот промокод"
        
        self.cursor.execute('UPDATE users SET balance = balance + ?, total_earned = total_earned + ? WHERE user_id = ?', 
                          (promo[1], promo[1], user_id))
        
        self.cursor.execute('UPDATE promocodes SET current_uses = current_uses + 1 WHERE code = ?', (code,))
        
        date = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.cursor.execute('INSERT INTO promocode_uses (user_id, code, date) VALUES (?, ?, ?)', 
                          (user_id, code, date))
        
        self.conn.commit()
        return True, f"✅ Промокод активирован! +{promo[1]} G"
    
    def get_all_promocodes(self):
        self.cursor.execute('SELECT * FROM promocodes')
        return self.cursor.fetchall()
    
    # ===== Задания =====
    def add_task(self, name, description, reward, type, target):
        date = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.cursor.execute('''
            INSERT INTO tasks (name, description, reward, type, target, created_date)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (name, description, reward, type, target, date))
        self.conn.commit()
        return self.cursor.lastrowid
    
    def get_tasks(self):
        self.cursor.execute('SELECT * FROM tasks ORDER BY id DESC')
        return self.cursor.fetchall()
    
    def get_task(self, task_id):
        self.cursor.execute('SELECT * FROM tasks WHERE id = ?', (task_id,))
        return self.cursor.fetchone()
    
    def delete_task(self, task_id):
        self.cursor.execute('DELETE FROM tasks WHERE id = ?', (task_id,))
        self.cursor.execute('DELETE FROM completed_tasks WHERE task_id = ?', (task_id,))
        self.conn.commit()
    
    def get_user_tasks(self, user_id):
        """Получает все задания с информацией о выполнении пользователем"""
        self.cursor.execute('''
            SELECT t.*, 
                   CASE WHEN ct.user_id IS NOT NULL THEN 1 ELSE 0 END as completed
            FROM tasks t
            LEFT JOIN completed_tasks ct ON t.id = ct.task_id AND ct.user_id = ?
            ORDER BY t.id DESC
        ''', (user_id,))
        return self.cursor.fetchall()
    
    def complete_task(self, user_id, task_id):
        # Проверяем, выполнял ли пользователь задание
        self.cursor.execute('SELECT * FROM completed_tasks WHERE user_id = ? AND task_id = ?', (user_id, task_id))
        if self.cursor.fetchone():
            return False, "❌ Вы уже выполняли это задание"
        
        # Получаем задание
        self.cursor.execute('SELECT * FROM tasks WHERE id = ?', (task_id,))
        task = self.cursor.fetchone()
        
        if not task:
            return False, "❌ Задание не найдено"
        
        # Начисляем награду
        self.cursor.execute('UPDATE users SET balance = balance + ?, total_earned = total_earned + ? WHERE user_id = ?', 
                          (task[3], task[3], user_id))
        
        # Записываем выполнение
        date = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.cursor.execute('INSERT INTO completed_tasks (user_id, task_id, date) VALUES (?, ?, ?)', 
                          (user_id, task_id, date))
        
        self.conn.commit()
        return True, f"✅ Задание выполнено! +{task[3]} G"
    
    def check_and_remove_task_completion(self, user_id, task_id):
        """Проверяет, подписан ли пользователь на канал, если нет - удаляет выполнение задания"""
        self.cursor.execute('SELECT * FROM completed_tasks WHERE user_id = ? AND task_id = ?', (user_id, task_id))
        completed = self.cursor.fetchone()
        
        if not completed:
            return False
        
        # Получаем задание
        self.cursor.execute('SELECT * FROM tasks WHERE id = ?', (task_id,))
        task = self.cursor.fetchone()
        
        if not task:
            return False
        
        # Если задание на подписку и пользователь отписался
        if task[4] == "subscription":
            # Проверка подписки будет выполнена в основном коде
            # Если отписался, удаляем запись о выполнении и возвращаем средства
            self.cursor.execute('DELETE FROM completed_tasks WHERE user_id = ? AND task_id = ?', (user_id, task_id))
            self.cursor.execute('UPDATE users SET balance = balance - ?, total_earned = total_earned - ? WHERE user_id = ?', 
                              (task[3], task[3], user_id))
            self.conn.commit()
            return True
        return False
    
    def get_user_completed_tasks(self, user_id):
        """Получает все задания, выполненные пользователем"""
        self.cursor.execute('''
            SELECT t.*, ct.date as completed_date
            FROM completed_tasks ct
            JOIN tasks t ON ct.task_id = t.id
            WHERE ct.user_id = ?
        ''', (user_id,))
        return self.cursor.fetchall()
    
    # ===== Вывод средств =====
    def create_withdraw_request(self, user_id, amount, wallet, screenshot_path):
        user = self.get_user(user_id)
        if user[3] < amount:
            return False, "❌ Недостаточно средств", None
        
        if amount < MIN_WITHDRAW:
            return False, f"❌ Минимальная сумма вывода {MIN_WITHDRAW} G", None
        
        self.cursor.execute('UPDATE users SET balance = balance - ? WHERE user_id = ?', (amount, user_id))
        
        date = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.cursor.execute('''
            INSERT INTO withdrawals (user_id, amount, wallet, screenshot_path, date)
            VALUES (?, ?, ?, ?, ?)
        ''', (user_id, amount, wallet, screenshot_path, date))
        self.conn.commit()
        
        withdrawal_id = self.cursor.lastrowid
        return True, "✅ Заявка на вывод создана! Ожидайте подтверждения", withdrawal_id
    
    def get_pending_withdrawals(self):
        self.cursor.execute('SELECT * FROM withdrawals WHERE status = "pending" ORDER BY date ASC')
        return self.cursor.fetchall()
    
    def get_withdrawal(self, withdrawal_id):
        self.cursor.execute('SELECT * FROM withdrawals WHERE id = ?', (withdrawal_id,))
        return self.cursor.fetchone()
    
    def complete_withdrawal(self, withdrawal_id):
        self.cursor.execute('UPDATE withdrawals SET status = "completed" WHERE id = ?', (withdrawal_id,))
        self.conn.commit()
        self.cursor.execute('SELECT * FROM withdrawals WHERE id = ?', (withdrawal_id,))
        return self.cursor.fetchone()
    
    def reject_withdrawal(self, withdrawal_id):
        self.cursor.execute('SELECT * FROM withdrawals WHERE id = ?', (withdrawal_id,))
        withdrawal = self.cursor.fetchone()
        
        if withdrawal:
            self.cursor.execute('UPDATE users SET balance = balance + ? WHERE user_id = ?', 
                               (withdrawal[2], withdrawal[1]))
            self.cursor.execute('UPDATE withdrawals SET status = "rejected" WHERE id = ?', (withdrawal_id,))
            self.conn.commit()
            return withdrawal
        return None
    
    # ===== Рефералы =====
    def get_referrals(self, user_id):
        self.cursor.execute('''
            SELECT u.user_id, u.username, u.first_name, r.date 
            FROM referrals r
            JOIN users u ON r.referral_id = u.user_id
            WHERE r.referrer_id = ?
            ORDER BY r.date DESC
        ''', (user_id,))
        return self.cursor.fetchall()
    
    # ===== Статистика =====
    def get_total_users(self):
        self.cursor.execute('SELECT COUNT(*) FROM users')
        return self.cursor.fetchone()[0]
    
    def get_total_balance(self):
        self.cursor.execute('SELECT SUM(balance) FROM users')
        result = self.cursor.fetchone()[0]
        return result if result else 0
    
    def get_total_tasks_completed(self):
        self.cursor.execute('SELECT COUNT(*) FROM completed_tasks')
        return self.cursor.fetchone()[0]

# Инициализация базы данных
db = Database()

# ================== FSM СОСТОЯНИЯ ==================
class AdminStates(StatesGroup):
    waiting_for_promocode = State()
    waiting_for_promocode_amount = State()
    waiting_for_promocode_uses = State()
    waiting_for_task_name = State()
    waiting_for_task_desc = State()
    waiting_for_task_reward = State()
    waiting_for_task_target = State()
    waiting_for_task_delete = State()
    waiting_for_channel = State()

class WithdrawStates(StatesGroup):
    waiting_for_amount = State()
    waiting_for_screenshot = State()
    waiting_for_wallet = State()

# ================== КЛАВИАТУРЫ ==================
def get_main_keyboard():
    """Главная клавиатура для пользователей - 5 кнопок"""
    keyboard = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="💰 Баланс"), KeyboardButton(text="👥 Рефералы")],
            [KeyboardButton(text="🎁 Задания"), KeyboardButton(text="💳 Вывод")],
            [KeyboardButton(text="👤 Мой профиль")]
        ],
        resize_keyboard=True
    )
    return keyboard

def get_admin_main_keyboard():
    """Главная клавиатура для админа - 5 кнопок + админка"""
    keyboard = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="💰 Баланс"), KeyboardButton(text="👥 Рефералы")],
            [KeyboardButton(text="🎁 Задания"), KeyboardButton(text="💳 Вывод")],
            [KeyboardButton(text="👤 Мой профиль"), KeyboardButton(text="👑 Админ панель")]
        ],
        resize_keyboard=True
    )
    return keyboard

def get_back_keyboard():
    """Клавиатура с кнопкой назад"""
    keyboard = ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="🔙 Назад в меню")]],
        resize_keyboard=True
    )
    return keyboard

def get_admin_panel_keyboard():
    """Клавиатура админ-панели"""
    keyboard = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📢 Управление каналами")],
            [KeyboardButton(text="🎫 Управление промокодами")],
            [KeyboardButton(text="📝 Управление заданиями")],
            [KeyboardButton(text="📋 Заявки на вывод")],
            [KeyboardButton(text="📊 Статистика бота")],
            [KeyboardButton(text="🔙 Назад в меню")]
        ],
        resize_keyboard=True
    )
    return keyboard

def get_channels_management_keyboard():
    keyboard = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="➕ Добавить канал")],
            [KeyboardButton(text="➖ Удалить канал")],
            [KeyboardButton(text="📋 Список каналов")],
            [KeyboardButton(text="🔙 Назад в админку")]
        ],
        resize_keyboard=True
    )
    return keyboard

def get_promocodes_management_keyboard():
    keyboard = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="➕ Создать промокод")],
            [KeyboardButton(text="📋 Список промокодов")],
            [KeyboardButton(text="🔙 Назад в админку")]
        ],
        resize_keyboard=True
    )
    return keyboard

def get_tasks_management_keyboard():
    keyboard = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="➕ Создать задание")],
            [KeyboardButton(text="➖ Удалить задание")],
            [KeyboardButton(text="📋 Список заданий")],
            [KeyboardButton(text="🔙 Назад в админку")]
        ],
        resize_keyboard=True
    )
    return keyboard

# ================== ПРОВЕРКА ПОДПИСКИ ==================
async def check_subscription(user_id, channel_username):
    """Проверяет, подписан ли пользователь на конкретный канал"""
    try:
        chat = await bot.get_chat(channel_username)
        member = await bot.get_chat_member(chat_id=chat.id, user_id=user_id)
        return member.status != 'left'
    except Exception as e:
        logging.error(f"Ошибка проверки подписки {channel_username}: {e}")
        return False

async def check_all_subscriptions(user_id):
    """Проверяет подписку на все обязательные каналы"""
    channels = db.get_channels()
    if not channels:
        return True
    
    for channel in channels:
        if not await check_subscription(user_id, channel[2]):
            return False
    return True

async def subscription_required(message: types.Message):
    """Проверяет подписку на обязательные каналы"""
    if not await check_all_subscriptions(message.from_user.id):
        channels = db.get_channels()
        keyboard = InlineKeyboardMarkup(inline_keyboard=[])
        
        for channel in channels:
            username = channel[2].replace('@', '')
            keyboard.inline_keyboard.append([
                InlineKeyboardButton(
                    text=f"📢 Подписаться на {channel[3]}", 
                    url=f"https://t.me/{username}"
                )
            ])
        
        keyboard.inline_keyboard.append([
            InlineKeyboardButton(text="✅ Проверить подписку", callback_data="check_sub")
        ])
        
        await message.answer(
            "❌ <b>Для использования бота необходимо подписаться на наши каналы!</b>",
            parse_mode="HTML",
            reply_markup=keyboard
        )
        return False
    return True

# ================== ФОНовая проверка отписок ==================
async def check_unsubscriptions():
    """Фоновая задача: проверяет, не отписались ли пользователи от каналов заданий"""
    while True:
        try:
            # Получаем все задания на подписку
            tasks = db.get_tasks()
            subscription_tasks = [t for t in tasks if t[4] == "subscription"]
            
            if subscription_tasks:
                # Получаем всех пользователей
                db.cursor.execute('SELECT user_id FROM users')
                users = db.cursor.fetchall()
                
                for user in users:
                    user_id = user[0]
                    # Получаем выполненные задания пользователя
                    completed_tasks = db.get_user_completed_tasks(user_id)
                    
                    for task in completed_tasks:
                        # Проверяем, подписан ли еще пользователь
                        is_subscribed = await check_subscription(user_id, task[5])
                        
                        if not is_subscribed:
                            # Пользователь отписался - снимаем задание и уменьшаем баланс
                            db.check_and_remove_task_completion(user_id, task[0])
                            logging.info(f"Пользователь {user_id} отписался от {task[5]}, задание #{task[0]} снято")
                            
                            # Уведомляем пользователя
                            try:
                                await bot.send_message(
                                    user_id,
                                    f"⚠️ <b>Внимание!</b>\n\n"
                                    f"Вы отписались от канала <b>{task[5]}</b>.\n"
                                    f"Задание <b>'{task[1]}'</b> было снято, а награда {task[3]} G списана с баланса.\n\n"
                                    f"Чтобы вернуть награду, подпишитесь снова и выполните задание заново.",
                                    parse_mode="HTML"
                                )
                            except:
                                pass
            
            await asyncio.sleep(CHECK_INTERVAL)
        except Exception as e:
            logging.error(f"Ошибка в фоновой проверке: {e}")
            await asyncio.sleep(60)

# ================== ХЕНДЛЕРЫ ==================

@dp.message(CommandStart())
async def cmd_start(message: types.Message):
    user_id = message.from_user.id
    username = message.from_user.username or "Нет юзернейма"
    first_name = message.from_user.first_name
    
    referrer_id = None
    args = message.text.split()
    if len(args) > 1:
        try:
            referrer_id = int(args[1].split('_')[1])
            if referrer_id == user_id:
                referrer_id = None
        except:
            pass
    
    db.add_user(user_id, username, first_name, referrer_id)
    
    if not await check_all_subscriptions(user_id):
        channels = db.get_channels()
        keyboard = InlineKeyboardMarkup(inline_keyboard=[])
        
        for channel in channels:
            username = channel[2].replace('@', '')
            keyboard.inline_keyboard.append([
                InlineKeyboardButton(
                    text=f"📢 Подписаться на {channel[3]}", 
                    url=f"https://t.me/{username}"
                )
            ])
        
        keyboard.inline_keyboard.append([
            InlineKeyboardButton(text="✅ Проверить подписку", callback_data="check_sub")
        ])
        
        await message.answer(
            f"👋 Привет, {first_name}!\n\n"
            "❌ <b>Для доступа к боту нужно подписаться на наши каналы!</b>",
            parse_mode="HTML",
            reply_markup=keyboard
        )
        return
    
    user = db.get_user(user_id)
    
    if user_id in ADMIN_IDS:
        keyboard = get_admin_main_keyboard()
    else:
        keyboard = get_main_keyboard()
    
    await message.answer(
        f"👋 Добро пожаловать, {first_name}!\n\n"
        f"💰 Твой баланс: {user[3]} G\n"
        f"👥 Приглашено друзей: {user[6]}\n\n"
        f"Используй кнопки ниже 👇",
        parse_mode="HTML",
        reply_markup=keyboard
    )

@dp.callback_query(F.data == "check_sub")
async def check_sub_callback(callback: types.CallbackQuery):
    if await check_all_subscriptions(callback.from_user.id):
        await callback.message.delete()
        user = db.get_user(callback.from_user.id)
        
        if callback.from_user.id in ADMIN_IDS:
            keyboard = get_admin_main_keyboard()
        else:
            keyboard = get_main_keyboard()
        
        await callback.message.answer(
            f"✅ <b>Подписка подтверждена!</b>\n\n"
            f"💰 Твой баланс: {user[3]} G\n"
            f"👥 Приглашено друзей: {user[6]}",
            parse_mode="HTML",
            reply_markup=keyboard
        )
    else:
        await callback.answer("❌ Вы еще не подписались на все каналы!", show_alert=True)

# ===== КНОПКА БАЛАНС =====
@dp.message(F.text == "💰 Баланс")
async def show_balance(message: types.Message):
    if not await subscription_required(message):
        return
    
    user = db.get_user(message.from_user.id)
    await message.answer(
        f"💰 <b>Твой баланс</b>\n\n"
        f"Доступно: {user[3]} G\n"
        f"Всего заработано: {user[4]} G\n\n"
        f"💳 Минимальный вывод: {MIN_WITHDRAW} G",
        parse_mode="HTML"
    )

# ===== КНОПКА РЕФЕРАЛЫ =====
@dp.message(F.text == "👥 Рефералы")
async def show_referrals(message: types.Message):
    if not await subscription_required(message):
        return
    
    referrals = db.get_referrals(message.from_user.id)
    user = db.get_user(message.from_user.id)
    bot_info = await bot.get_me()
    ref_link = f"https://t.me/{bot_info.username}?start=ref_{message.from_user.id}"
    
    text = (
        f"👥 <b>Реферальная система</b>\n\n"
        f"👥 Приглашено: {user[6]}\n"
        f"💰 Заработано: {user[6] * REFERRAL_BONUS} G\n"
        f"🎁 Бонус за друга: {REFERRAL_BONUS} G\n\n"
        f"🔗 <b>Твоя ссылка:</b>\n"
        f"<code>{ref_link}</code>\n\n"
    )
    
    if referrals:
        text += "<b>📋 Список рефералов:</b>\n"
        for i, ref in enumerate(referrals, 1):
            text += f"{i}. {ref[2]} (@{ref[1]}) - {ref[3]}\n"
    else:
        text += "У тебя пока нет рефералов."
    
    await message.answer(text, parse_mode="HTML")

# ===== КНОПКА ЗАДАНИЯ =====
@dp.message(F.text == "🎁 Задания")
async def show_tasks(message: types.Message):
    if not await subscription_required(message):
        return
    
    tasks = db.get_user_tasks(message.from_user.id)
    
    if not tasks:
        await message.answer("🎁 Пока нет доступных заданий!")
        return
    
    text = "🎁 <b>Доступные задания</b>\n\n"
    keyboard = InlineKeyboardMarkup(inline_keyboard=[])
    
    for task in tasks:
        # Проверяем, подписан ли пользователь на канал задания (для активной проверки)
        if task[4] == "subscription" and task[7] == 0:
            is_subscribed = await check_subscription(message.from_user.id, task[5])
            if is_subscribed:
                # Если подписан, но задание не отмечено выполненным - автоматически выполняем
                success, msg = db.complete_task(message.from_user.id, task[0])
                if success:
                    await message.answer(
                        f"✅ Задание '{task[1]}' выполнено автоматически! +{task[3]} G",
                        parse_mode="HTML"
                    )
                    await show_tasks(message)
                    return
        
        status = "✅" if task[7] == 1 else "⏳"
        text += f"{status} <b>{task[1]}</b> - {task[3]} G\n"
        text += f"└ {task[2]}\n\n"
        
        if task[7] == 0:
            keyboard.inline_keyboard.append([
                InlineKeyboardButton(
                    text=f"👉 {task[1]}", 
                    callback_data=f"task_{task[0]}"
                )
            ])
    
    await message.answer(text, parse_mode="HTML", reply_markup=keyboard)

@dp.callback_query(F.data.startswith("task_"))
async def complete_task_callback(callback: types.CallbackQuery):
    task_id = int(callback.data.split("_")[1])
    task = db.get_task(task_id)
    
    if not task:
        await callback.answer("❌ Задание не найдено", show_alert=True)
        return
    
    if task[4] == "subscription":
        is_subscribed = await check_subscription(callback.from_user.id, task[5])
        
        if is_subscribed:
            success, msg = db.complete_task(callback.from_user.id, task_id)
            await callback.answer(msg, show_alert=True)
            
            if success:
                await callback.message.delete()
                await show_tasks(callback.message)
        else:
            username = task[5].replace('@', '')
            keyboard = InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="📢 Подписаться", url=f"https://t.me/{username}")],
                    [InlineKeyboardButton(text="✅ Проверить", callback_data=f"check_task_{task_id}")]
                ]
            )
            await callback.message.edit_text(
                f"Для выполнения задания подпишись на канал:\n{task[5]}",
                reply_markup=keyboard
            )

@dp.callback_query(F.data.startswith("check_task_"))
async def check_task_callback(callback: types.CallbackQuery):
    task_id = int(callback.data.split("_")[2])
    task = db.get_task(task_id)
    
    is_subscribed = await check_subscription(callback.from_user.id, task[5])
    
    if is_subscribed:
        success, msg = db.complete_task(callback.from_user.id, task_id)
        await callback.answer(msg, show_alert=True)
        
        if success:
            await callback.message.delete()
            await show_tasks(callback.message)
    else:
        await callback.answer("❌ Вы еще не подписались!", show_alert=True)

# ===== КНОПКА ВЫВОД =====
@dp.message(F.text == "💳 Вывод")
async def withdraw_start(message: types.Message, state: FSMContext):
    if not await subscription_required(message):
        return
    
    user = db.get_user(message.from_user.id)
    
    if user[3] < MIN_WITHDRAW:
        await message.answer(
            f"❌ Минимальная сумма вывода {MIN_WITHDRAW} G\n"
            f"Твой баланс: {user[3]} G"
        )
        return
    
    await state.set_state(WithdrawStates.waiting_for_amount)
    await message.answer(
        f"💰 Введите сумму для вывода (мин. {MIN_WITHDRAW} G):\n"
        f"Доступно: {user[3]} G",
        reply_markup=get_back_keyboard()
    )

@dp.message(WithdrawStates.waiting_for_amount)
async def withdraw_amount(message: types.Message, state: FSMContext):
    if message.text == "🔙 Назад в меню":
        await state.clear()
        if message.from_user.id in ADMIN_IDS:
            await message.answer("Главное меню", reply_markup=get_admin_main_keyboard())
        else:
            await message.answer("Главное меню", reply_markup=get_main_keyboard())
        return
    
    try:
        amount = float(message.text)
        user = db.get_user(message.from_user.id)
        
        if amount < MIN_WITHDRAW:
            await message.answer(f"❌ Минимальная сумма {MIN_WITHDRAW} G")
            return
        
        if amount > user[3]:
            await message.answer("❌ Недостаточно средств")
            return
        
        await state.update_data(amount=amount)
        await state.set_state(WithdrawStates.waiting_for_screenshot)
        await message.answer(
            "📸 Отправьте скриншот вашего объявления на рынке:",
            reply_markup=get_back_keyboard()
        )
        
    except ValueError:
        await message.answer("❌ Введите корректное число")

@dp.message(WithdrawStates.waiting_for_screenshot, F.photo)
async def withdraw_screenshot(message: types.Message, state: FSMContext):
    photo = message.photo[-1]
    file_name = f"screenshots/withdraw_{message.from_user.id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jpg"
    
    file = await bot.get_file(photo.file_id)
    await bot.download_file(file.file_path, file_name)
    
    await state.update_data(screenshot_path=file_name)
    await state.set_state(WithdrawStates.waiting_for_wallet)
    
    await message.answer(
        "✅ Скриншот сохранен!\n\n"
        "📝 Введите паттерн/скин для вывода:",
        reply_markup=get_back_keyboard()
    )

@dp.message(WithdrawStates.waiting_for_screenshot)
async def withdraw_screenshot_invalid(message: types.Message):
    await message.answer(
        "❌ Отправьте скриншот в виде изображения!",
        reply_markup=get_back_keyboard()
    )

@dp.message(WithdrawStates.waiting_for_wallet)
async def withdraw_wallet(message: types.Message, state: FSMContext):
    if message.text == "🔙 Назад в меню":
        await state.clear()
        if message.from_user.id in ADMIN_IDS:
            await message.answer("Главное меню", reply_markup=get_admin_main_keyboard())
        else:
            await message.answer("Главное меню", reply_markup=get_main_keyboard())
        return
    
    data = await state.get_data()
    success, msg, withdrawal_id = db.create_withdraw_request(
        message.from_user.id,
        data['amount'],
        message.text,
        data['screenshot_path']
    )
    
    await state.clear()
    
    if message.from_user.id in ADMIN_IDS:
        keyboard = get_admin_main_keyboard()
    else:
        keyboard = get_main_keyboard()
    
    await message.answer(msg, reply_markup=keyboard)
    
    if success:
        user = db.get_user(message.from_user.id)
        
        for admin_id in ADMIN_IDS:
            if admin_id != message.from_user.id:
                try:
                    with open(data['screenshot_path'], 'rb') as photo:
                        await bot.send_photo(admin_id, photo, caption=f"📸 Заявка #{withdrawal_id}")
                    
                    admin_keyboard = InlineKeyboardMarkup(
                        inline_keyboard=[
                            [
                                InlineKeyboardButton(text="✅ Подтвердить", callback_data=f"admin_confirm_{withdrawal_id}"),
                                InlineKeyboardButton(text="❌ Отклонить", callback_data=f"admin_reject_{withdrawal_id}")
                            ]
                        ]
                    )
                    
                    await bot.send_message(
                        admin_id,
                        f"🚨 <b>ЗАЯВКА #{withdrawal_id}</b>\n\n"
                        f"👤 @{message.from_user.username}\n"
                        f"💰 {data['amount']} G\n"
                        f"💳 {message.text}",
                        parse_mode="HTML",
                        reply_markup=admin_keyboard
                    )
                except Exception as e:
                    logging.error(f"Ошибка: {e}")

# ===== КНОПКА МОЙ ПРОФИЛЬ =====
@dp.message(F.text == "👤 Мой профиль")
async def show_profile(message: types.Message):
    if not await subscription_required(message):
        return
    
    user = db.get_user(message.from_user.id)
    bot_info = await bot.get_me()
    ref_link = f"https://t.me/{bot_info.username}?start=ref_{message.from_user.id}"
    
    text = (
        f"👤 <b>Мой профиль</b>\n\n"
        f"🆔 ID: <code>{message.from_user.id}</code>\n"
        f"👤 Имя: {message.from_user.first_name}\n"
        f"💰 Баланс: {user[3]} G\n"
        f"💵 Всего заработано: {user[4]} G\n"
        f"👥 Приглашено: {user[6]} чел.\n\n"
        f"🔗 <b>Реферальная ссылка:</b>\n"
        f"<code>{ref_link}</code>"
    )
    
    await message.answer(text, parse_mode="HTML")

# ===== АДМИН-ПАНЕЛЬ =====
@dp.message(F.text == "👑 Админ панель")
async def admin_panel(message: types.Message):
    if message.from_user.id not in ADMIN_IDS:
        await message.answer("❌ У вас нет прав")
        return
    
    await message.answer(
        "👑 <b>Админ-панель</b>",
        parse_mode="HTML",
        reply_markup=get_admin_panel_keyboard()
    )

# ===== УПРАВЛЕНИЕ КАНАЛАМИ =====
@dp.message(F.text == "📢 Управление каналами")
async def manage_channels(message: types.Message):
    if message.from_user.id not in ADMIN_IDS:
        return
    await message.answer("Управление каналами", reply_markup=get_channels_management_keyboard())

@dp.message(F.text == "➕ Добавить канал")
async def add_channel_start(message: types.Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        return
    await state.set_state(AdminStates.waiting_for_channel)
    await message.answer("Отправьте username канала (например @channel):", reply_markup=get_back_keyboard())

@dp.message(AdminStates.waiting_for_channel)
async def add_channel_process(message: types.Message, state: FSMContext):
    if message.text == "🔙 Назад в меню":
        await state.clear()
        await message.answer("Управление каналами", reply_markup=get_channels_management_keyboard())
        return
    
    channel_username = message.text.strip()
    if not channel_username.startswith('@'):
        channel_username = '@' + channel_username
    
    if db.channel_exists(channel_username):
        await message.answer("❌ Канал уже добавлен!")
        await state.clear()
        return
    
    try:
        username_for_chat = channel_username.replace('@', '')
        chat = await bot.get_chat(username_for_chat)
        channel_title = chat.title
        channel_id = chat.id
        
        db.add_channel(channel_id, channel_username, channel_title)
        await message.answer(f"✅ Канал {channel_title} добавлен!", reply_markup=get_channels_management_keyboard())
    except Exception:
        await message.answer("❌ Ошибка: бот не админ канала")
    
    await state.clear()

@dp.message(F.text == "📋 Список каналов")
async def list_channels(message: types.Message):
    if message.from_user.id not in ADMIN_IDS:
        return
    
    channels = db.get_channels()
    if not channels:
        await message.answer("Список каналов пуст")
        return
    
    text = "📋 Каналы:\n\n"
    for ch in channels:
        text += f"• {ch[3]} - {ch[2]}\n"
    await message.answer(text)

@dp.message(F.text == "➖ Удалить канал")
async def remove_channel_start(message: types.Message):
    if message.from_user.id not in ADMIN_IDS:
        return
    
    channels = db.get_channels()
    if not channels:
        await message.answer("Нет каналов для удаления")
        return
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[])
    for ch in channels:
        keyboard.inline_keyboard.append([
            InlineKeyboardButton(text=f"❌ {ch[3]}", callback_data=f"remove_channel_{ch[0]}")
        ])
    
    await message.answer("Выберите канал:", reply_markup=keyboard)

@dp.callback_query(F.data.startswith("remove_channel_"))
async def remove_channel_callback(callback: types.CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("Нет прав")
        return
    
    ch_id = int(callback.data.split("_")[2])
    db.cursor.execute('SELECT * FROM required_channels WHERE id = ?', (ch_id,))
    channel = db.cursor.fetchone()
    
    if channel:
        db.remove_channel(channel[1])
        await callback.message.edit_text(f"✅ Канал {channel[3]} удален")
    else:
        await callback.answer("Канал не найден")

# ===== УПРАВЛЕНИЕ ПРОМОКОДАМИ =====
@dp.message(F.text == "🎫 Управление промокодами")
async def manage_promocodes(message: types.Message):
    if message.from_user.id not in ADMIN_IDS:
        return
    await message.answer("Управление промокодами", reply_markup=get_promocodes_management_keyboard())

@dp.message(F.text == "➕ Создать промокод")
async def create_promo_start(message: types.Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        return
    await state.set_state(AdminStates.waiting_for_promocode)
    await message.answer("Введите код промокода:", reply_markup=get_back_keyboard())

@dp.message(AdminStates.waiting_for_promocode)
async def create_promo_code(message: types.Message, state: FSMContext):
    if message.text == "🔙 Назад в меню":
        await state.clear()
        await message.answer("Управление промокодами", reply_markup=get_promocodes_management_keyboard())
        return
    
    await state.update_data(code=message.text.upper())
    await state.set_state(AdminStates.waiting_for_promocode_amount)
    await message.answer("Введите сумму награды:")

@dp.message(AdminStates.waiting_for_promocode_amount)
async def create_promo_amount(message: types.Message, state: FSMContext):
    try:
        amount = float(message.text)
        await state.update_data(amount=amount)
        await state.set_state(AdminStates.waiting_for_promocode_uses)
        await message.answer("Введите лимит использований:")
    except ValueError:
        await message.answer("❌ Введите число")

@dp.message(AdminStates.waiting_for_promocode_uses)
async def create_promo_uses(message: types.Message, state: FSMContext):
    try:
        uses = int(message.text)
        data = await state.get_data()
        db.create_promocode(data['code'], data['amount'], uses)
        await state.clear()
        await message.answer(f"✅ Промокод {data['code']} создан! (+{data['amount']} G)", reply_markup=get_promocodes_management_keyboard())
    except ValueError:
        await message.answer("❌ Введите число")

@dp.message(F.text == "📋 Список промокодов")
async def list_promocodes(message: types.Message):
    if message.from_user.id not in ADMIN_IDS:
        return
    
    promos = db.get_all_promocodes()
    if not promos:
        await message.answer("Нет промокодов")
        return
    
    text = "🎫 Промокоды:\n\n"
    for p in promos:
        text += f"• {p[0]} - {p[1]} G (использовано {p[3]}/{p[2]})\n"
    await message.answer(text)

# ===== УПРАВЛЕНИЕ ЗАДАНИЯМИ =====
@dp.message(F.text == "📝 Управление заданиями")
async def manage_tasks(message: types.Message):
    if message.from_user.id not in ADMIN_IDS:
        return
    await message.answer("Управление заданиями", reply_markup=get_tasks_management_keyboard())

@dp.message(F.text == "➕ Создать задание")
async def create_task_start(message: types.Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        return
    await state.set_state(AdminStates.waiting_for_task_name)
    await message.answer("Введите название задания:", reply_markup=get_back_keyboard())

@dp.message(AdminStates.waiting_for_task_name)
async def create_task_name(message: types.Message, state: FSMContext):
    if message.text == "🔙 Назад в меню":
        await state.clear()
        await message.answer("Управление заданиями", reply_markup=get_tasks_management_keyboard())
        return
    
    await state.update_data(name=message.text)
    await state.set_state(AdminStates.waiting_for_task_desc)
    await message.answer("Введите описание задания:")

@dp.message(AdminStates.waiting_for_task_desc)
async def create_task_desc(message: types.Message, state: FSMContext):
    await state.update_data(desc=message.text)
    await state.set_state(AdminStates.waiting_for_task_reward)
    await message.answer("Введите награду (в G):")

@dp.message(AdminStates.waiting_for_task_reward)
async def create_task_reward(message: types.Message, state: FSMContext):
    try:
        reward = float(message.text)
        await state.update_data(reward=reward)
        await state.set_state(AdminStates.waiting_for_task_target)
        await message.answer("Введите username канала (например @channel):")
    except ValueError:
        await message.answer("❌ Введите число")

@dp.message(AdminStates.waiting_for_task_target)
async def create_task_target(message: types.Message, state: FSMContext):
    data = await state.get_data()
    target = message.text
    if not target.startswith('@'):
        target = '@' + target
    
    db.add_task(data['name'], data['desc'], data['reward'], "subscription", target)
    await state.clear()
    await message.answer(f"✅ Задание '{data['name']}' создано! (+{data['reward']} G)", reply_markup=get_tasks_management_keyboard())

@dp.message(F.text == "📋 Список заданий")
async def list_tasks_admin(message: types.Message):
    if message.from_user.id not in ADMIN_IDS:
        return
    
    tasks = db.get_tasks()
    if not tasks:
        await message.answer("Нет заданий")
        return
    
    text = "📋 Задания:\n\n"
    for t in tasks:
        text += f"ID {t[0]}: {t[1]} - {t[3]} G\n"
        text += f"  {t[2]}\n"
        text += f"  Канал: {t[5]}\n\n"
    await message.answer(text)

@dp.message(F.text == "➖ Удалить задание")
async def delete_task_start(message: types.Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        return
    await state.set_state(AdminStates.waiting_for_task_delete)
    await message.answer("Введите ID задания для удаления:", reply_markup=get_back_keyboard())

@dp.message(AdminStates.waiting_for_task_delete)
async def delete_task_process(message: types.Message, state: FSMContext):
    if message.text == "🔙 Назад в меню":
        await state.clear()
        await message.answer("Управление заданиями", reply_markup=get_tasks_management_keyboard())
        return
    
    try:
        task_id = int(message.text)
        task = db.get_task(task_id)
        if task:
            db.delete_task(task_id)
            await message.answer(f"✅ Задание '{task[1]}' удалено!")
        else:
            await message.answer("❌ Задание не найдено")
        await state.clear()
        await message.answer("Управление заданиями", reply_markup=get_tasks_management_keyboard())
    except ValueError:
        await message.answer("❌ Введите ID")

# ===== ЗАЯВКИ НА ВЫВОД =====
@dp.message(F.text == "📋 Заявки на вывод")
async def show_withdrawals(message: types.Message):
    if message.from_user.id not in ADMIN_IDS:
        return
    
    withdrawals = db.get_pending_withdrawals()
    if not withdrawals:
        await message.answer("Нет заявок")
        return
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[])
    for w in withdrawals:
        user = db.get_user(w[1])
        username = user[1] if user else "Unknown"
        keyboard.inline_keyboard.append([
            InlineKeyboardButton(text=f"💰 {w[2]} G - @{username}", callback_data=f"withdraw_{w[0]}")
        ])
    
    await message.answer("📋 Заявки на вывод:", reply_markup=keyboard)

@dp.callback_query(F.data.startswith("withdraw_"))
async def process_withdrawal(callback: types.CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("Нет прав")
        return
    
    w_id = int(callback.data.split("_")[1])
    withdrawal = db.get_withdrawal(w_id)
    
    if not withdrawal:
        await callback.answer("Заявка не найдена")
        return
    
    user = db.get_user(withdrawal[1])
    
    try:
        with open(withdrawal[4], 'rb') as photo:
            await callback.message.answer_photo(photo, caption=f"📸 Заявка #{w_id}")
    except:
        pass
    
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Подтвердить", callback_data=f"confirm_{w_id}"),
                InlineKeyboardButton(text="❌ Отклонить", callback_data=f"reject_{w_id}")
            ]
        ]
    )
    
    await callback.message.edit_text(
        f"📬 Заявка #{w_id}\n"
        f"👤 @{user[1]}\n"
        f"💰 {withdrawal[2]} G\n"
        f"💳 {withdrawal[3]}",
        reply_markup=keyboard
    )

@dp.callback_query(F.data.startswith("confirm_"))
async def confirm_withdrawal(callback: types.CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        return
    
    w_id = int(callback.data.split("_")[1])
    withdrawal = db.complete_withdrawal(w_id)
    
    if withdrawal:
        try:
            await bot.send_message(withdrawal[1], f"✅ Вывод {withdrawal[2]} G подтвержден!")
        except:
            pass
        await callback.message.edit_text(f"✅ Заявка #{w_id} подтверждена")
    else:
        await callback.answer("Ошибка")

@dp.callback_query(F.data.startswith("reject_"))
async def reject_withdrawal(callback: types.CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        return
    
    w_id = int(callback.data.split("_")[1])
    withdrawal = db.reject_withdrawal(w_id)
    
    if withdrawal:
        try:
            if os.path.exists(withdrawal[4]):
                os.remove(withdrawal[4])
        except:
            pass
        
        try:
            await bot.send_message(withdrawal[1], f"❌ Вывод {withdrawal[2]} G отклонен")
        except:
            pass
        
        await callback.message.edit_text(f"❌ Заявка #{w_id} отклонена")
    else:
        await callback.answer("Ошибка")

# ===== СТАТИСТИКА БОТА =====
@dp.message(F.text == "📊 Статистика бота")
async def bot_stats(message: types.Message):
    if message.from_user.id not in ADMIN_IDS:
        return
    
    total_users = db.get_total_users()
    total_balance = db.get_total_balance()
    channels = db.get_channels()
    pending = len(db.get_pending_withdrawals())
    tasks = len(db.get_tasks())
    completed = db.get_total_tasks_completed()
    
    text = (
        f"📊 Статистика\n\n"
        f"👥 Пользователей: {total_users}\n"
        f"💰 Общий баланс: {total_balance} G\n"
        f"📢 Каналов: {len(channels)}\n"
        f"🎁 Заданий: {tasks}\n"
        f"✅ Выполнено: {completed}\n"
        f"⏳ Выводов: {pending}"
    )
    
    await message.answer(text)

# ===== НАВИГАЦИЯ =====
@dp.message(F.text == "🔙 Назад в меню")
async def back_to_menu(message: types.Message):
    if message.from_user.id in ADMIN_IDS:
        await message.answer("Главное меню", reply_markup=get_admin_main_keyboard())
    else:
        await message.answer("Главное меню", reply_markup=get_main_keyboard())

@dp.message(F.text == "🔙 Назад в админку")
async def back_to_admin(message: types.Message):
    if message.from_user.id in ADMIN_IDS:
        await message.answer("Админ-панель", reply_markup=get_admin_panel_keyboard())

# ================== ЗАПУСК БОТА ==================
async def main():
    logging.info("Бот запущен...")
    
    # Запускаем фоновую проверку отписок
    asyncio.create_task(check_unsubscriptions())
    
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
