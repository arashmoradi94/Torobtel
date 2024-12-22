# config.py
import os
from dotenv import load_dotenv

load_dotenv()
# Telegram Bot Token
BOT_TOKEN = os.getenv('BOT_TOKEN')

# Database Configuration
DATABASE_URL = os.getenv('DATABASE_URL', 'sqlite:///bot.db')

# Encryption key for storing API keys
ENCRYPTION_KEY = os.getenv('ENCRYPTION_KEY', 'your-secure-encryption-key')

# Admin user IDs
ADMIN_IDS = list(map(int, os.getenv('ADMIN_IDS', '').split(',')))

# Rate limiting
MAX_REQUESTS_PER_MINUTE = 60

# License/Subscription settings
FREE_TIER_LIMIT = 10  # Number of free requests per day
SUBSCRIPTION_TIERS = {
    'basic': {
        'price': 100000,  # Monthly price in Tomans
        'features': ['woocommerce_price', 'basic_comparison']
    },
    'premium': {
        'price': 300000,
        'features': ['woocommerce_price', 'torob_comparison', 'auto_price_adjust', 'reporting']
    }
}

# database.py
from sqlalchemy import create_engine, Column, Integer, String, Boolean, DateTime, ForeignKey
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship
from datetime import datetime
from cryptography.fernet import Fernet
from config import DATABASE_URL, ENCRYPTION_KEY

Base = declarative_base()
engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(bind=engine)

class User(Base):
    __tablename__ = "users"
    
    id = Column(Integer, primary_key=True)
    telegram_id = Column(Integer, unique=True)
    username = Column(String)
    subscription_type = Column(String, default='free')
    subscription_end_date = Column(DateTime)
    is_active = Column(Boolean, default=True)
    woo_api_key = Column(String)
    woo_api_secret = Column(String)
    torob_api_key = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)

class License(Base):
    __tablename__ = "licenses"
    
    id = Column(Integer, primary_key=True)
    license_key = Column(String, unique=True)
    user_id = Column(Integer, ForeignKey('users.id'))
    type = Column(String)
    is_active = Column(Boolean, default=True)
    expires_at = Column(DateTime)
    created_at = Column(DateTime, default=datetime.utcnow)

    user = relationship("User")

# Create all tables
Base.metadata.create_all(engine)

# utils.py
import hashlib
import uuid
from datetime import datetime, timedelta
from cryptography.fernet import Fernet
from config import ENCRYPTION_KEY

class Encryption:
    def __init__(self):
        self.fernet = Fernet(ENCRYPTION_KEY.encode())
    
    def encrypt(self, data: str) -> str:
        return self.fernet.encrypt(data.encode()).decode()
    
    def decrypt(self, data: str) -> str:
        return self.fernet.decrypt(data.encode()).decode()

def generate_license_key():
    return str(uuid.uuid4())

def validate_license_key(license_key: str, db_session):
    license = db_session.query(License).filter_by(
        license_key=license_key,
        is_active=True
    ).first()
    
    if not license:
        return False
        
    if license.expires_at and license.expires_at < datetime.utcnow():
        license.is_active = False
        db_session.commit()
        return False
        
    return True

# api_handlers.py
import requests
from woocommerce import API
import json
from bs4 import BeautifulSoup

class WooCommerceHandler:
    def __init__(self, api_key, api_secret, store_url):
        self.wcapi = API(
            url=store_url,
            consumer_key=api_key,
            consumer_secret=api_secret,
            version="wc/v3"
        )
    
    def get_product_price(self, product_name):
        try:
            products = self.wcapi.get("products", params={"search": product_name}).json()
            if products:
                return {
                    'id': products[0]['id'],
                    'name': products[0]['name'],
                    'price': float(products[0]['price']),
                    'regular_price': float(products[0]['regular_price']) if products[0]['regular_price'] else None
                }
            return None
        except Exception as e:
            print(f"WooCommerce API Error: {str(e)}")
            return None

class TorobHandler:
    def __init__(self, api_key):
        self.api_key = api_key
        self.base_url = "https://api.torob.com/v4"
        
    def get_product_price(self, product_name):
        try:
            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json"
            }
            
            search_response = requests.get(
                f"{self.base_url}/search",
                params={"q": product_name},
                headers=headers
            )
            
            data = search_response.json()
            if data.get('results'):
                return {
                    'name': data['results'][0]['name'],
                    'price': data['results'][0]['price'],
                    'shop_name': data['results'][0]['shop_name']
                }
            return None
        except Exception as e:
            print(f"Torob API Error: {str(e)}")
            return None

# bot.py
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters
)
from database import SessionLocal, User, License
from api_handlers import WooCommerceHandler, TorobHandler
from utils import Encryption, generate_license_key, validate_license_key
from config import BOT_TOKEN, ADMIN_IDS
import asyncio

class PriceComparisonBot:
    def __init__(self):
        self.encryption = Encryption()
        self.application = Application.builder().token(BOT_TOKEN).build()
        self.setup_handlers()

    def setup_handlers(self):
        self.application.add_handler(CommandHandler("start", self.start_command))
        self.application.add_handler(CallbackQueryHandler(self.button_callback))
        self.application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_message))

    async def start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        keyboard = [
            [InlineKeyboardButton("تنظیمات API", callback_data='api_settings')],
            [InlineKeyboardButton("جستجوی قیمت محصول", callback_data='search_product')],
            [InlineKeyboardButton("تنظیمات پیشرفته", callback_data='advanced_settings')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(
            "به ربات مقایسه قیمت خوش آمدید!\n"
            "لطفاً یکی از گزینه‌های زیر را انتخاب کنید:",
            reply_markup=reply_markup
        )

    async def button_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()

        if query.data == 'api_settings':
            keyboard = [
                [InlineKeyboardButton("تنظیم API ووکامرس", callback_data='set_woo_api')],
                [InlineKeyboardButton("تنظیم API ترب", callback_data='set_torob_api')],
                [InlineKeyboardButton("بازگشت به منوی اصلی", callback_data='main_menu')]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await query.message.edit_text(
                "لطفاً نوع API را انتخاب کنید:",
                reply_markup=reply_markup
            )

    async def handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        message_text = update.message.text

        with SessionLocal() as db_session:
            user = db_session.query(User).filter_by(telegram_id=user_id).first()
            
            if not user:
                await update.message.reply_text("لطفاً ابتدا با دستور /start ربات را راه‌اندازی کنید.")
                return

            # Handle different states based on context.user_data
            current_state = context.user_data.get('state')
            
            if current_state == 'awaiting_product_search':
                await self.handle_product_search(update, context, user, message_text)
            elif current_state == 'awaiting_woo_api':
                await self.handle_woo_api_setup(update, context, user, message_text)
            elif current_state == 'awaiting_torob_api':
                await self.handle_torob_api_setup(update, context, user, message_text)

    async def handle_product_search(self, update, context, user, product_name):
        woo_handler = WooCommerceHandler(
            self.encryption.decrypt(user.woo_api_key),
            self.encryption.decrypt(user.woo_api_secret),
            user.store_url
        )
        torob_handler = TorobHandler(self.encryption.decrypt(user.torob_api_key))

        woo_price = woo_handler.get_product_price(product_name)
        torob_price = torob_handler.get_product_price(product_name)

        if woo_price and torob_price:
            message = (
                f"نتایج جستجو برای {product_name}:\n\n"
                f"قیمت در فروشگاه شما: {woo_price['price']:,} تومان\n"
                f"کمترین قیمت در ترب: {torob_price['price']:,} تومان\n"
                f"فروشگاه با کمترین قیمت: {torob_price['shop_name']}"
            )
        else:
            message = "متأسفانه محصول مورد نظر یافت نشد."

        await update.message.reply_text(message)
        context.user_data['state'] = None

    def run(self):
        self.application.run_polling()

if __name__ == "__main__":
    bot = PriceComparisonBot()
    bot.run()

