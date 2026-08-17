#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
ERA X BOT - Telegram Version (performance build)
Features:
- 🔍 APK Firebase Scanner
- 🔄 Panel Exchanger (Convert any panel to ERA X)
- 📦 Bulk Scanning (50+ APKs)
- 👑 Admin Dashboard
- 📊 Statistics
- 🔑 Firebase Key Management
- 💾 File Download Support
- 💾 User/Scan data synced to Firebase Realtime Database + local JSON backup

v2 Changes:
- NO inline keyboard buttons. Everything is auto-detected.
- Send APK -> scanned automatically
- Send panel link (URL) -> converted automatically
- Send ZIP -> bulk scanned automatically
- /start, /help, /stats, /keys, /admin kept as text commands
"""

import os
import re
import json
import zipfile
import tempfile
import shutil
import subprocess
import asyncio
import time
import base64
import hashlib
import html
import logging
from pathlib import Path
from datetime import datetime
from urllib.parse import urlparse, parse_qs, unquote

import requests as req_lib
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.request import HTTPXRequest
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters,
    ContextTypes,
)

# ==================== CONFIGURATION ====================
PANEL_URL = "https://eraxpanel.vercel.app/"
FIREBASE_ONLY_API_FALLBACK = "ERA"
FORCE_JOIN_CHANNELS = ("@eraXarmy", "@eraXearning")
MAX_BULK_SIZE = 50
MAX_FILE_SIZE = 50 * 1024 * 1024
DOWNLOAD_TIMEOUT = 60

# Telegram custom emoji IDs supplied by the owner. The list contains 35 entries,
# with one repeated ID; unique IDs are mapped in source-order and any unmapped
# emoji keeps its normal Unicode fallback so messages never fail to send.
CUSTOM_EMOJI_IDS = [
    '6176966310920983412', '5463172695132745432', '6267008582294705964',
    '6267000941547885720', '5231012545799666522', '6267039884016358504',
    '5271604874419647061', '5240241223632954241', '5462921117423384478',
    '6267129592998270736', '5292226786229236118', '6089090515540644835',
    '6267039884016358504', '5282843764451195532', '6264537399846507987', '5463402896789898195',
    '5453957997418004470', '6267229004311303657', '5314289371803850879',
    '5445378843193922298', '5416041192905265756', '5839375134061761681',
    '6269377265348383859', '6120425102283643012', '6269322397141177131',
    '6267140231632262769', '5460755126761312667', '5244837092042750681',
    '4904848288345228262', '6267186750423045640', '5222444124698853913',
    '6264907690451932671', '6147654280112248427', '5440621591387980068',
    '5816442787544964208',
]
CUSTOM_EMOJI_GLYPHS = [
    '🔑', '✅', '📦', '❌', '🔍', '⛔', '🔗', '🚫', '📊', '🛠', '📣',
    '🔄', '👑', '⚠', '🌐', '➜', '🆔', '👥', '♻', '💾', '🎆', '🏠',
    '📎', '🟢', '📈', '🚩', '🤖', '🔥', '🎁', '🔴', '🚀', '⚡', '📖',
    '📁', '👤', '📅', '🕐',
]
CUSTOM_EMOJI_MAP = dict(zip(CUSTOM_EMOJI_GLYPHS, CUSTOM_EMOJI_IDS))

def custom_emoji(glyph):
    """Return a Telegram HTML custom-emoji entity with Unicode fallback."""
    emoji_id = CUSTOM_EMOJI_MAP.get(glyph)
    if emoji_id:
        return f'<tg-emoji emoji-id="{emoji_id}">{glyph}</tg-emoji>'
    return glyph

# Directories
DATA_DIR = Path("data")
USERS_DIR = DATA_DIR / "users"
SCANS_DIR = DATA_DIR / "scans"
BULK_DIR = DATA_DIR / "bulk"
SESSIONS_DIR = DATA_DIR / "sessions"
ADMIN_DIR = DATA_DIR / "admin"
FIREBASE_KEYS_DIR = DATA_DIR / "firebase_keys"
PANEL_LOGS_DIR = DATA_DIR / "panel_logs"
TEMP_DIR = DATA_DIR / "temp"

for dir_path in [DATA_DIR, USERS_DIR, SCANS_DIR, BULK_DIR, SESSIONS_DIR,
                 ADMIN_DIR, FIREBASE_KEYS_DIR, PANEL_LOGS_DIR, TEMP_DIR]:
    dir_path.mkdir(parents=True, exist_ok=True)

# Logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

BOT_STATE_FILE = ADMIN_DIR / 'bot_state.json'

def _read_bot_state():
    default = {'enabled': True, 'maintenance': False, 'updated_at': None}
    try:
        if BOT_STATE_FILE.exists():
            data = json.loads(BOT_STATE_FILE.read_text())
            if isinstance(data, dict):
                default.update(data)
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning('Could not read bot state: %s', exc)
    return default

def _write_bot_state(**changes):
    state = _read_bot_state()
    state.update(changes)
    state['updated_at'] = datetime.now().isoformat()
    BOT_STATE_FILE.write_text(json.dumps(state, indent=2))
    return state


# ==================== FIREBASE SYNC ====================
class FirebaseSync:
    """Fire-and-forget sync of user/scan data to Firebase Realtime Database."""

    @staticmethod
    def _safe_push(session, url, data):
        try:
            session.put(url, json=data, timeout=15)
        except Exception as e:
            logger.warning(f"Firebase sync failed for {url}: {e}")

    @classmethod
    def save_user(cls, session, user_id, user_data):
        url = f"{FIREBASE_DB_URL}/users/{user_id}.json"
        cls._safe_push(session, url, user_data)

    @classmethod
    def save_scan(cls, session, user_id, scan_id, scan_data):
        url = f"{FIREBASE_DB_URL}/scans/{user_id}/{scan_id}.json"
        cls._safe_push(session, url, scan_data)

    @classmethod
    def save_panel(cls, session, user_id, panel_id, panel_data):
        url = f"{FIREBASE_DB_URL}/panels/{user_id}/{panel_id}.json"
        cls._safe_push(session, url, panel_data)

    @classmethod
    def save_firebase_key(cls, session, key_id, key_data):
        url = f"{FIREBASE_DB_URL}/firebase_keys/{key_id}.json"
        cls._safe_push(session, url, key_data)


# ==================== DATABASE ====================
class Database:
    @staticmethod
    def get_user(user_id):
        path = USERS_DIR / f"{user_id}.json"
        if path.exists():
            with open(path, 'r') as f:
                return json.load(f)
        return {'user_id': user_id, 'scans': 0, 'panels': 0,
                'joined': datetime.now().isoformat()}

    @staticmethod
    def save_user(user_id, data):
        path = USERS_DIR / f"{user_id}.json"
        with open(path, 'w') as f:
            json.dump(data, f, indent=2)

    @staticmethod
    def is_banned(user_id):
        return bool(Database.get_user(user_id).get('banned', False))

    @staticmethod
    def set_banned(user_id, banned=True):
        user = Database.get_user(user_id)
        user['banned'] = bool(banned)
        user['ban_updated'] = datetime.now().isoformat()
        Database.save_user(user_id, user)
        return user

    @staticmethod
    def get_user_ids():
        ids = []
        for user in Database.get_all_users():
            try:
                ids.append(int(user['user_id']))
            except (KeyError, TypeError, ValueError):
                continue
        return ids

    @staticmethod
    def save_scan(user_id, scan_data, firebase_session=None):
        user = Database.get_user(user_id)
        user['scans'] = user.get('scans', 0) + 1
        user['last_scan'] = datetime.now().isoformat()
        user.setdefault('username', scan_data.get('username'))
        Database.save_user(user_id, user)

        scan_data['timestamp'] = datetime.now().isoformat()
        scan_data['scan_id'] = hashlib.md5(
            f"{user_id}{time.time()}".encode()).hexdigest()[:8]

        path = SCANS_DIR / f"{user_id}.json"
        scans = []
        if path.exists():
            with open(path, 'r') as f:
                try:
                    scans = json.load(f)
                except json.JSONDecodeError:
                    scans = []
                if not isinstance(scans, list):
                    scans = []

        scans.append(scan_data)

        if len(scans) > 100:
            scans = scans[-100:]

        with open(path, 'w') as f:
            json.dump(scans, f, indent=2)

        if firebase_session is not None:
            FirebaseSync.save_scan(firebase_session, user_id,
                                   scan_data['scan_id'], scan_data)

        if scan_data.get('found') and scan_data.get('api_key'):
            key_data = {
                'api_key': scan_data.get('api_key'),
                'project_id': scan_data.get('project_id'),
                'app_id': scan_data.get('app_id'),
                'user_id': user_id,
                'scanned_at': datetime.now().isoformat()
            }
            key_path = FIREBASE_KEYS_DIR / f"{scan_data['api_key'][:10]}.json"
            with open(key_path, 'w') as f:
                json.dump(key_data, f, indent=2)
            if firebase_session is not None:
                FirebaseSync.save_firebase_key(
                    firebase_session, scan_data['api_key'][:10], key_data)

    @staticmethod
    def save_panel(user_id, panel_data, firebase_session=None):
        user = Database.get_user(user_id)
        user['panels'] = user.get('panels', 0) + 1
        Database.save_user(user_id, user)

        panel_data['timestamp'] = datetime.now().isoformat()
        panel_data['panel_id'] = hashlib.md5(
            f"{user_id}{time.time()}".encode()).hexdigest()[:8]

        path = PANEL_LOGS_DIR / f"{user_id}.json"
        panels = []
        if path.exists():
            with open(path, 'r') as f:
                try:
                    panels = json.load(f)
                except json.JSONDecodeError:
                    panels = []
                if not isinstance(panels, list):
                    panels = []

        panels.append(panel_data)

        if len(panels) > 50:
            panels = panels[-50:]

        with open(path, 'w') as f:
            json.dump(panels, f, indent=2)

        if firebase_session is not None:
            FirebaseSync.save_panel(firebase_session, user_id,
                                    panel_data['panel_id'], panel_data)

    @staticmethod
    def get_scans(user_id, limit=50):
        path = SCANS_DIR / f"{user_id}.json"
        if path.exists():
            try:
                with open(path, 'r') as f:
                    scans = json.load(f)
                if not isinstance(scans, list):
                    return []
                return scans[-limit:] if len(scans) > limit else scans
            except json.JSONDecodeError:
                return []
        return []

    @staticmethod
    def get_panels(user_id, limit=50):
        path = PANEL_LOGS_DIR / f"{user_id}.json"
        if path.exists():
            try:
                with open(path, 'r') as f:
                    panels = json.load(f)
                if not isinstance(panels, list):
                    return []
                return panels[-limit:] if len(panels) > limit else panels
            except json.JSONDecodeError:
                return []
        return []

    @staticmethod
    def get_all_scans():
        all_scans = []
        for path in SCANS_DIR.glob('*.json'):
            try:
                with open(path, 'r') as f:
                    scans = json.load(f)
                if not isinstance(scans, list):
                    continue
                for scan in scans:
                    scan['user_id'] = path.stem
                all_scans.extend(scans)
            except json.JSONDecodeError:
                continue
        return sorted(all_scans, key=lambda x: x.get('timestamp', ''),
                      reverse=True)

    @staticmethod
    def get_all_users():
        users = []
        for path in USERS_DIR.glob('*.json'):
            try:
                with open(path, 'r') as f:
                    users.append(json.load(f))
            except json.JSONDecodeError:
                continue
        return users

    @staticmethod
    def get_firebase_keys(user_id=None):
        """Return Firebase records globally for admins or only for one user."""
        if user_id is not None:
            records = []
            seen = set()
            # Scan records are authoritative for user ownership.
            for item in Database.get_scans(user_id):
                if not item.get('found'):
                    continue
                record = {
                    'api_key': item.get('api_key'),
                    'database_url': item.get('database_url') or item.get('firebase_url'),
                    'project_id': item.get('project_id'),
                    'app_id': item.get('app_id'),
                    'user_id': user_id,
                    'scanned_at': item.get('timestamp') or item.get('scanned_at'),
                }
                signature = (record.get('database_url') or '', record.get('api_key') or '', record.get('project_id') or '')
                if signature not in seen:
                    seen.add(signature)
                    records.append(record)
            # Panel submissions are also owned by their submitting user.
            for item in Database.get_panels(user_id):
                if not (item.get('api_key') or item.get('database_url')):
                    continue
                record = {
                    'api_key': item.get('api_key'),
                    'database_url': item.get('database_url'),
                    'project_id': item.get('project_id'),
                    'app_id': item.get('app_id'),
                    'user_id': user_id,
                    'scanned_at': item.get('timestamp'),
                }
                signature = (record.get('database_url') or '', record.get('api_key') or '', record.get('project_id') or '')
                if signature not in seen:
                    seen.add(signature)
                    records.append(record)
            return records

        keys = []
        for path in FIREBASE_KEYS_DIR.glob('*.json'):
            try:
                with open(path, 'r') as f:
                    keys.append(json.load(f))
            except (OSError, json.JSONDecodeError):
                continue
        return keys

    @staticmethod
    def get_stats():
        users = len(list(USERS_DIR.glob('*.json')))
        scans = 0
        for path in SCANS_DIR.glob('*.json'):
            try:
                with open(path, 'r') as f:
                    data = json.load(f)
                if isinstance(data, list):
                    scans += len(data)
            except json.JSONDecodeError:
                continue
        panels = 0
        for path in PANEL_LOGS_DIR.glob('*.json'):
            try:
                with open(path, 'r') as f:
                    data = json.load(f)
                if isinstance(data, list):
                    panels += len(data)
            except json.JSONDecodeError:
                continue
        keys = len(list(FIREBASE_KEYS_DIR.glob('*.json')))
        return {'users': users, 'scans': scans, 'panels': panels, 'keys': keys}


# ==================== FIREBASE SCANNER ====================
class FirebaseScanner:
    # Broad patterns - key anchors kept loose so obfuscated / minified content matches
    API_KEY_PATTERN = re.compile(r'AIzaSy[A-Za-z0-9_-]{30,40}')
    PROJECT_ID_CONTEXT_PATTERN = re.compile(
        r'["\'>\s]?project[_ ]?id["\']?\s*[:=]\s*["\'>\s]?([a-z0-9][a-z0-9-]{5,62}[a-z0-9])', re.IGNORECASE)
    PROJECT_ID_BARE_PATTERN = re.compile(r'([a-z0-9][a-z0-9-]{5,62}[a-z0-9])\.firebaseio\.com', re.IGNORECASE)
    PROJECT_ID_BARE_PATTERN2 = re.compile(r'([a-z0-9][a-z0-9-]{5,62}[a-z0-9])\.firebaseapp\.com', re.IGNORECASE)
    APP_ID_PATTERN = re.compile(r'1:\d{8,12}:android:[a-f0-9]{8,16}(?::[a-f0-9]+)?', re.IGNORECASE)
    APP_ID_PATTERN2 = re.compile(r'["\']?app[_ ]?id["\']?\s*[:=]\s*["\']?(\d+:\d+:\w+:[a-f0-9]+)["\']?', re.IGNORECASE)
    SENDER_ID_PATTERN = re.compile(r'messaging[_ ]?sender[_ ]?id["\']?\s*[:=\'>\s]+["\']?(\d{8,15})', re.IGNORECASE)
    SENDER_ID_BARE_PATTERN = re.compile(r'"(\d{8,15})"', re.IGNORECASE)
    STORAGE_BUCKET_PATTERN = re.compile(
        r'["\'>\s]?storage[_ ]?bucket["\']?\s*[:=]\s*["\'>\s]?([A-Za-z0-9.-]+\.appspot\.com)', re.IGNORECASE)
    STORAGE_BUCKET_BARE = re.compile(r'([a-z0-9][a-z0-9-]{4,62}[a-z0-9])\.appspot\.com', re.IGNORECASE)
    AUTH_DOMAIN_PATTERN = re.compile(
        r'["\'>\s]?auth[_ ]?domain["\']?\s*[:=]\s*["\'>\s]?([A-Za-z0-9.-]+\.firebaseapp\.com)', re.IGNORECASE)
    MEASUREMENT_ID_PATTERN = re.compile(r'G-[A-Z0-9]{6,12}')
    DATABASE_URL_PATTERN = re.compile(
        r'["\'>\s]?database[_ ]?url["\']?\s*[:=]\s*["\'>\s]?(https?://[A-Za-z0-9.-]+\.(?:firebaseio(?:-derived)?\.com|firebasedatabase\.app))',
        re.IGNORECASE)
    DATABASE_URL_BARE = re.compile(r'([A-Za-z0-9.-]+)\.firebaseio\.com', re.IGNORECASE)
    DATABASE_URL_BARE2 = re.compile(r'([A-Za-z0-9.-]+\.firebasedatabase\.app)', re.IGNORECASE)
    FIREBASE_CONFIG_PATTERN = re.compile(r'firebaseConfig\s*=\s*\{([^}]+)\}', re.DOTALL)
    FIREBASE_CONFIG_PATTERN2 = re.compile(r'apiKey\s*[:=]\s*["\']?([A-Za-z0-9_-]+)["\']?', re.IGNORECASE)

    # Extensions of text-like files worth scanning inside the APK
    TEXT_EXTS = ('.json', '.xml', '.js', '.html', '.htm', '.txt', '.yml', '.yaml',
                 '.properties', '.ini', '.cfg', '.plist', '.map', '.svg', '.css', '.htm', '.dart')

    def __init__(self, apk_path):
        self.apk_path = Path(apk_path)
        self.temp_dir = None
        self.data = {
            'api_key': None,
            'project_id': None,
            'app_id': None,
            'storage_bucket': None,
            'auth_domain': None,
            'messaging_sender_id': None,
            'measurement_id': None,
            'database_url': None,
            'found': False,
            'method': None
        }

    def scan(self):
        # Fast path: raw bytes and APK text resources cover normal Firebase configs.
        self._extract_python_strings()
        if not self.data['found']:
            self._extract_from_zip()
        # External decompilers are optional and can add tens of seconds. Do not
        # invoke them in the normal request path; raw/ZIP extraction is bounded.
        if self.temp_dir and self.temp_dir.exists():
            shutil.rmtree(self.temp_dir, ignore_errors=True)
        return self.data

    def _extract_python_strings(self):
        """Extract printable strings from the raw APK bytes (no external tool needed)."""
        try:
            with open(self.apk_path, 'rb') as f:
                raw = f.read()
            # UTF-8 printable runs
            text8 = re.sub(rb'[\x00-\x08\x0e-\x1f\x7f-\xff]', b' ', raw).decode('ascii', errors='ignore')
            self._find_patterns(text8)
            # UTF-16LE printable runs (common in Android resource tables)
            text16 = raw.decode('utf-16-le', errors='ignore')
            text16 = re.sub(r'[\x00-\x08\x0e-\x1f\x7f-\x9f]', ' ', text16)
            self._find_patterns(text16)
            if self.data['found']:
                self.data['method'] = self.data.get('method') or 'raw_strings'
        except Exception as e:
            logger.error(f"raw strings error: {e}")

    def _extract_from_zip(self):
        try:
            with zipfile.ZipFile(self.apk_path, 'r') as apk:
                for name in apk.namelist():
                    lower = name.lower()
                    # google-services files first (highest signal)
                    is_gs = 'google-services' in lower
                    is_text = lower.endswith(self.TEXT_EXTS)
                    is_assets_config = ('assets' in lower and
                                        ('firebase' in lower or 'config' in lower or 'settings' in lower))
                    if not (is_gs or is_text or is_assets_config):
                        continue
                    try:
                        content = apk.read(name).decode('utf-8', errors='ignore')
                    except Exception:
                        continue
                    if lower.endswith('.json'):
                        # Try structured JSON parse
                        try:
                            self._extract_from_json(json.loads(content))
                        except Exception:
                            self._find_patterns(content)
                    else:
                        self._find_patterns(content)
                    if self.data['found']:
                        self.data['method'] = f"zip:{Path(name).name}"
                        return
        except Exception as e:
            logger.error(f"zip error: {e}")

    def _extract_with_apktool(self):
        try:
            self.temp_dir = Path(tempfile.mkdtemp())
            subprocess.run(
                ['apktool', 'd', str(self.apk_path), '-o', str(self.temp_dir), '-f'],
                capture_output=True, timeout=120
            )

            xml_path = self.temp_dir / 'res' / 'values' / 'google-services.xml'
            if xml_path.exists():
                with open(xml_path, 'r', encoding='utf-8', errors='ignore') as f:
                    self._find_patterns(f.read())
                    if self.data['found']:
                        self.data['method'] = 'apktool'
                        return

            # Scan all decompiled text files
            for fpath in self.temp_dir.rglob('*'):
                if fpath.is_file() and fpath.suffix.lower() in self.TEXT_EXTS:
                    try:
                        content = fpath.read_text(encoding='utf-8', errors='ignore')
                        self._find_patterns(content)
                        if self.data['found']:
                            self.data['method'] = f"apktool:{fpath.name}"
                            return
                    except OSError:
                        continue
        except FileNotFoundError:
            logger.warning("apktool not installed, skipping decompile step")
        except Exception as e:
            logger.error(f"apktool error: {e}")

    def _extract_from_dex(self):
        try:
            result = subprocess.run(
                ['dexdump', str(self.apk_path)],
                capture_output=True, text=True, timeout=60
            )
            if result.stdout:
                self._find_patterns(result.stdout)
                if self.data['found']:
                    self.data['method'] = 'dexdump'
        except FileNotFoundError:
            logger.warning("dexdump not available, skipping dex dump")
        except Exception as e:
            logger.error(f"dexdump error: {e}")

    def _find_patterns(self, content):
        """Match every pattern against content; keeps best (longest/most specific) values."""
        def set_if(key, value):
            if value and not self.data[key]:
                self.data[key] = value
                self.data['found'] = True

        set_if('api_key', self.API_KEY_PATTERN.search(content)
               and self.API_KEY_PATTERN.search(content).group(0))

        m = self.PROJECT_ID_CONTEXT_PATTERN.search(content)
        set_if('project_id', m.group(1) if m else None)
        if not self.data['project_id']:
            m = self.PROJECT_ID_BARE_PATTERN.search(content)
            set_if('project_id', m.group(1) if m else None)
        if not self.data['project_id']:
            m = self.PROJECT_ID_BARE_PATTERN2.search(content)
            set_if('project_id', m.group(1) if m else None)
        if not self.data['project_id']:
            m = re.search(r'project[_ ]?id["\']?\s*[:=\'>\s]+["\']?([a-z0-9][a-z0-9-]{5,62})\b',
                          content, re.IGNORECASE)
            set_if('project_id', m.group(1) if m else None)
        # Android XML: <string name="project_id">VALUE</string>
        if not self.data['project_id']:
            m = re.search(r'<string\s+name="project_id">([a-z0-9][a-z0-9-]{5,62}[a-z0-9])<',
                          content, re.IGNORECASE)
            set_if('project_id', m.group(1) if m else None)

        m = self.APP_ID_PATTERN.search(content)
        set_if('app_id', m.group(0) if m else None)
        if not self.data['app_id']:
            m = self.APP_ID_PATTERN2.search(content)
            set_if('app_id', m.group(1) if m else None)

        m = self.SENDER_ID_PATTERN.search(content)
        set_if('messaging_sender_id', m.group(1) if m else None)
        if not self.data['messaging_sender_id']:
            m = self.MEASUREMENT_ID_PATTERN.search(content)
            set_if('messaging_sender_id', m.group(0) if m else None)

        m = self.STORAGE_BUCKET_PATTERN.search(content)
        set_if('storage_bucket', m.group(1) if m else None)
        if not self.data['storage_bucket']:
            m = self.STORAGE_BUCKET_BARE.search(content)
            set_if('storage_bucket', m.group(1) if m else None)

        m = self.AUTH_DOMAIN_PATTERN.search(content)
        set_if('auth_domain', m.group(1) if m else None)

        m = self.MEASUREMENT_ID_PATTERN.search(content)
        set_if('measurement_id', m.group(0) if m else None)

        m = self.DATABASE_URL_PATTERN.search(content)
        set_if('database_url', m.group(1) if m else None)
        if not self.data['database_url']:
            m = self.DATABASE_URL_BARE.search(content)
            set_if('database_url', m.group(1) if m else None)
        if not self.data['database_url']:
            m = self.DATABASE_URL_BARE2.search(content)
            set_if('database_url', m.group(1) if m else None)
        # Normalize bare host tokens into full DB URLs
        if self.data['database_url'] and not re.match(r'https?://', self.data['database_url']):
            host = self.data['database_url']
            if 'firebasedatabase.app' in host:
                self.data['database_url'] = f"https://{host}"
            else:
                self.data['database_url'] = f"https://{host}.firebaseio.com"

        # firebaseConfig = { ... } block
        config_match = self.FIREBASE_CONFIG_PATTERN.search(content)
        if config_match:
            self._find_patterns(config_match.group(1))

        # Bare apiKey: "..." style used in some configs (fallback only)
        if not self.data['api_key']:
            m = self.FIREBASE_CONFIG_PATTERN2.search(content)
            cand = m.group(1) if m else None
            if cand and cand.startswith('AIzaSy'):
                set_if('api_key', cand)

    def _extract_from_json(self, data):
        try:
            if 'client' in data:
                client = data['client'][0] if isinstance(data['client'], list) else data['client']
                if 'api_key' in client:
                    api_keys = client['api_key']
                    if isinstance(api_keys, list) and api_keys:
                        self.data['api_key'] = api_keys[0].get('current_key')
                        self.data['found'] = True
                if 'client_info' in client:
                    self.data['app_id'] = client['client_info'].get('mobilesdk_app_id')
            if 'project_info' in data:
                self.data['project_id'] = data['project_info'].get('project_id')
                self.data['storage_bucket'] = data['project_info'].get('storage_bucket')
        except Exception as e:
            logger.error(f"json extraction error: {e}")

    def get_panel_link(self):
        params = []
        if self.data['api_key']:
            params.append(f"api_key={self.data['api_key']}")
        if self.data['project_id']:
            params.append(f"project_id={self.data['project_id']}")
        if self.data['app_id']:
            params.append(f"app_id={self.data['app_id']}")
        if self.data['storage_bucket']:
            params.append(f"storage_bucket={self.data['storage_bucket']}")
        if self.data['auth_domain']:
            params.append(f"auth_domain={self.data['auth_domain']}")
        if self.data['messaging_sender_id']:
            params.append(f"sender_id={self.data['messaging_sender_id']}")

        return f"{PANEL_URL}?{'&'.join(params)}" if params else PANEL_URL


# ==================== PANEL EXCHANGER ====================
class PanelExchanger:
    URL_PATTERN = re.compile(r'https?://\S+', re.IGNORECASE)
    API_KEY_STANDALONE_PATTERN = re.compile(r'AIzaSy[a-zA-Z0-9_-]{33}')

    @staticmethod
    def decode_panel_link(link):
        try:
            parsed = urlparse(link)
            query = parse_qs(parsed.query)

            firebase_data = {
                'api_key': None,
                'project_id': None,
                'app_id': None,
                'storage_bucket': None,
                'auth_domain': None,
                'sender_id': None,
                'database_url': None,
                'found': False
            }

            # Plain Firebase database URLs are valid inputs, not only encoded panel links.
            raw_data = PanelExchanger._parse_firebase_data(link)
            if raw_data.get('database_url'):
                raw_url = str(raw_data['database_url']).strip()
                if not re.match(r'https?://', raw_url, re.IGNORECASE):
                    raw_data['database_url'] = f"https://{raw_url}"
                raw_data['api_key'] = raw_data.get('api_key') or FIREBASE_ONLY_API_FALLBACK
                raw_data['found'] = True
                return raw_data

            if 's' in query:
                encoded = query['s'][0]
                try:
                    decoded = base64.b64decode(encoded).decode('utf-8')
                except Exception:
                    return firebase_data
                parts = [p.strip() for p in decoded.split('|||') if p.strip()]
                if len(parts) >= 2:
                    # ERA X BOT share payload: DB_URL|||API_KEY (or DB_URL|||Ok)
                    part0 = parts[0]
                    part1 = parts[1]
                    if 'firebaseio.com' in part0 or 'firebasedatabase.app' in part0 or '.app' in part0:
                        firebase_data['database_url'] = part0
                        if part1 and part1.lower() != 'ok':
                            firebase_data['api_key'] = part1
                        else:
                            firebase_data['api_key'] = FIREBASE_ONLY_API_FALLBACK
                        firebase_data['found'] = True
                        # Try to extract project id from DB url host
                        m = re.match(r'https?://([A-Za-z0-9.-]+)\.', part0)
                        if m:
                            firebase_data['project_id'] = m.group(1).split('-default-rtdb')[0]
                        return firebase_data
                    # Fallback: legacy multi-part payload
                    firebase_data = PanelExchanger._decode_payload_parts(parts)
                    return firebase_data
                # Fallback: treat whole decoded string as a single payload
                firebase_data = PanelExchanger._parse_firebase_data(decoded)
                if firebase_data.get('found'):
                    return firebase_data

            for key in query:
                if 'api' in key.lower():
                    firebase_data['api_key'] = query[key][0]
                    firebase_data['found'] = True
                elif 'project' in key.lower():
                    firebase_data['project_id'] = query[key][0]
                    firebase_data['found'] = True
                elif key.lower() in ('app', 'app_id'):
                    firebase_data['app_id'] = query[key][0]
                    firebase_data['found'] = True
                elif 'storage' in key.lower():
                    firebase_data['storage_bucket'] = query[key][0]
                    firebase_data['found'] = True
                elif key.lower() in ('auth', 'auth_domain'):
                    firebase_data['auth_domain'] = query[key][0]
                    firebase_data['found'] = True
                elif key.lower() in ('sender', 'sender_id'):
                    firebase_data['sender_id'] = query[key][0]
                    firebase_data['found'] = True
                elif 'db' in key.lower() or 'database' in key.lower():
                    firebase_data['database_url'] = query[key][0]
                    firebase_data['found'] = True

            # Raw API key anywhere in query/path
            if not firebase_data.get('api_key'):
                for m in PanelExchanger.API_KEY_STANDALONE_PATTERN.finditer(link):
                    firebase_data['api_key'] = m.group(0)
                    firebase_data['found'] = True

            return firebase_data

        except Exception as e:
            logger.error(f"Panel decode error: {e}")
            return None

    @staticmethod
    def _classify_part(part):
        """Classify a bare payload segment into a firebase field."""
        if re.match(r'AIzaSy[a-zA-Z0-9_-]{33}$', part):
            return 'api_key', part
        if re.match(r'\d+:\d+:android:[a-f0-9]+$', part):
            return 'app_id', part
        if re.match(r'^\d{10,15}$', part):
            return 'sender_id', part
        if re.match(r'[A-Za-z0-9\-\.]+\.firebaseio\.com$', part, re.IGNORECASE):
            return 'database_url', part
        if re.match(r'[a-z0-9\-]+\.appspot\.com$', part, re.IGNORECASE):
            return 'storage_bucket', part
        if re.match(r'[a-z0-9\-]+\.firebaseapp\.com$', part, re.IGNORECASE):
            return 'project_id', part
        if part.isalnum() or '-' in part:
            # Generic project-id-like token
            return 'project_id', part
        return None, None

    @staticmethod
    def _decode_payload_parts(parts):
        """Decode an |||-separated payload: [api_key, project_id, app_id, ...]."""
        firebase_data = {
            'api_key': None,
            'project_id': None,
            'app_id': None,
            'storage_bucket': None,
            'auth_domain': None,
            'sender_id': None,
            'database_url': None,
            'found': False
        }
        order = ['api_key', 'project_id', 'app_id', 'storage_bucket',
                 'auth_domain', 'sender_id', 'database_url']
        for i, part in enumerate(parts):
            kind, value = PanelExchanger._classify_part(part)
            if kind:
                firebase_data[kind] = value
                firebase_data['found'] = True
            elif i < len(order):
                # Positional fallback: assume standard encode order
                candidate = order[i]
                firebase_data[candidate] = part
                firebase_data['found'] = True
        # Post-process: fix ambiguous assignments using patterns
        for key in ('project_id', 'auth_domain', 'storage_bucket', 'database_url'):
            val = firebase_data.get(key)
            if val and firebase_data.get('project_id'):
                m = re.match(r'[a-z0-9\-]+\.firebaseapp\.com$', val, re.IGNORECASE)
                if m:
                    firebase_data['auth_domain'] = val
                m2 = re.match(r'[a-z0-9\-]+\.appspot\.com$', val, re.IGNORECASE)
                if m2:
                    firebase_data['storage_bucket'] = val
                m3 = re.match(r'[A-Za-z0-9\-\.]+\.firebaseio\.com$', val, re.IGNORECASE)
                if m3:
                    firebase_data['database_url'] = val
        return firebase_data

    @staticmethod
    def _parse_firebase_data(data):
        firebase_data = {
            'api_key': None,
            'project_id': None,
            'app_id': None,
            'storage_bucket': None,
            'auth_domain': None,
            'sender_id': None,
            'database_url': None,
            'found': False
        }

        patterns = {
            'api_key': re.compile(r'AIzaSy[a-zA-Z0-9_-]{33}'),
            'project_id': re.compile(r'([a-z0-9\-]+)\.firebaseapp\.com', re.IGNORECASE),
            'app_id': re.compile(r'\d+:\d+:android:[a-f0-9]+'),
            'storage_bucket': re.compile(r'([a-z0-9\-]+)\.appspot\.com', re.IGNORECASE),
            'auth_domain': re.compile(r'([a-z0-9\-]+)\.firebaseapp\.com', re.IGNORECASE),
            'sender_id': re.compile(r'\d{10,15}'),
        }

        for key, pattern in patterns.items():
            match = pattern.search(data)
            if match:
                value = match.group(1) if match.groups() else match.group(0)
                firebase_data[key] = value
                firebase_data['found'] = True

        for m in re.finditer(r'([A-Za-z0-9\-\.]+\.firebaseio\.com)', data, re.IGNORECASE):
            if not firebase_data.get('database_url'):
                firebase_data['database_url'] = m.group(1)
                firebase_data['found'] = True
        # Also catch asia-southeast1 style firebasedatabase.app URLs
        for m in re.finditer(r'(https?://[A-Za-z0-9.-]+\.firebasedatabase\.app)', data, re.IGNORECASE):
            if not firebase_data.get('database_url'):
                firebase_data['database_url'] = m.group(1)
                firebase_data['found'] = True
        for m in re.finditer(r'([A-Za-z0-9.-]+\.firebasedatabase\.app)', data, re.IGNORECASE):
            if not firebase_data.get('database_url'):
                firebase_data['database_url'] = m.group(1)
                firebase_data['found'] = True

        # Fallback for app_id without android: prefix (e.g. bare numeric id)
        if not firebase_data.get('app_id'):
            m = re.search(r'^[a-f0-9]{32}$', data.strip())
            if m:
                firebase_data['app_id'] = m.group(0)
                firebase_data['found'] = True

        return firebase_data

    @staticmethod
    def _db_url_of(firebase_data):
        """Return the best normalized Firebase Database URL from scan/exchange data."""
        url = firebase_data.get('database_url')
        if not url:
            proj = firebase_data.get('project_id')
            if proj:
                return f"https://{proj}-default-rtdb.firebaseio.com"
            return None
        url = str(url).strip()
        if re.match(r'https?://', url, re.IGNORECASE):
            return url
        if re.search(r'\.(?:firebaseio\.com|firebasedatabase\.app)$', url, re.IGNORECASE):
            return f"https://{url}"
        return f"https://{url}.firebaseio.com"

    @staticmethod
    def generate_encoded_link(firebase_data):
        """Build the ERA X BOT share link: ?s= base64(DB_URL|||API_KEY)."""
        db_url = PanelExchanger._db_url_of(firebase_data)
        api_key = firebase_data.get('api_key') or FIREBASE_ONLY_API_FALLBACK
        if not db_url:
            return None
        payload = f"{db_url}|||{api_key}"
        encoded = base64.b64encode(payload.encode()).decode()
        return f"{PANEL_URL}?s={encoded}"

    @staticmethod
    def generate_era_panel(firebase_data):
        params = []
        if firebase_data.get('api_key'):
            params.append(f"api_key={firebase_data['api_key']}")
        if firebase_data.get('project_id'):
            params.append(f"project_id={firebase_data['project_id']}")
        if firebase_data.get('app_id'):
            params.append(f"app_id={firebase_data['app_id']}")
        if firebase_data.get('storage_bucket'):
            params.append(f"storage_bucket={firebase_data['storage_bucket']}")
        if firebase_data.get('auth_domain'):
            params.append(f"auth_domain={firebase_data['auth_domain']}")
        if firebase_data.get('sender_id'):
            params.append(f"sender_id={firebase_data['sender_id']}")
        if firebase_data.get('database_url'):
            params.append(f"database_url={firebase_data['database_url']}")

        return f"{PANEL_URL}?{'&'.join(params)}" if params else PANEL_URL




# ==================== TELEGRAM BOT ====================
class Bot:
    def __init__(self, firebase_session):
        self.application = None
        self.firebase_session = firebase_session
        self.busy_users = set()
        self.user_last_message_ids = {}

    async def _clear_user_message(self, msg):
        """Delete the previous tracked bot message for a normal user, if possible."""
        if not msg or not getattr(msg, 'chat_id', None):
            return
        user_id = getattr(getattr(msg, 'from_user', None), 'id', None)
        if not user_id or self._is_admin(user_id):
            return
        message_id = self.user_last_message_ids.pop(user_id, None)
        if not message_id:
            return
        try:
            await msg.get_bot().delete_message(chat_id=msg.chat_id, message_id=message_id)
        except Exception:
            pass

    @staticmethod
    def _plain_text(text):
        """Remove Telegram HTML/custom-emoji markup for a safe fallback."""
        text = re.sub(r'<tg-emoji\b[^>]*>(.*?)</tg-emoji>', r'\1', str(text), flags=re.S)
        text = re.sub(r'</?(?:b|strong|i|em|u|s|code|pre|blockquote|a)(?:\s[^>]*)?>', '', text, flags=re.I)
        return html.unescape(text)

    async def _send_user(self, msg, text, **kwargs):
        """Send a user-facing message after removing the previous tracked one."""
        user_id = getattr(getattr(msg, 'from_user', None), 'id', None)
        await self._clear_user_message(msg)
        try:
            sent = await msg.get_bot().send_message(chat_id=msg.chat_id, text=text, **kwargs)
        except Exception:
            fallback = self._plain_text(text)
            sent = await msg.get_bot().send_message(
                chat_id=msg.chat_id, text=fallback, parse_mode=None,
                reply_markup=kwargs.get('reply_markup'))
        if user_id and not self._is_admin(user_id):
            self.user_last_message_ids[user_id] = sent.message_id
        return sent

    def _is_admin(self, user_id):
        return user_id in ADMIN_IDS

    async def _send(self, msg, text, **kwargs):
        """Send safely; custom emoji is optional and never blocks delivery."""
        try:
            return await msg.reply_text(text, **kwargs)
        except Exception:
            return await msg.reply_text(text, parse_mode=None)

    async def _missing_force_channels(self, update, context):
        """Return only channels the user has not joined yet."""
        user_id = update.effective_user.id
        if self._is_admin(user_id):
            return []
        missing = []
        for channel in FORCE_JOIN_CHANNELS:
            try:
                member = await context.bot.get_chat_member(channel, user_id)
                status = str(getattr(member, "status", "")).lower()
                joined = status in {"creator", "administrator", "member"}
                if status == "restricted":
                    joined = bool(getattr(member, "is_member", False))
                if not joined:
                    missing.append(channel)
            except Exception as exc:
                logger.warning("Force-join check failed for %s: %s", channel, exc)
                # Do not silently bypass force-join when a configured channel
                # cannot be checked; require that channel until Telegram replies.
                missing.append(channel)
        return missing

    async def _is_force_joined(self, update, context):
        return not await self._missing_force_channels(update, context)

    async def _delete_callback_message(self, query):
        try:
            await query.message.delete()
        except Exception:
            pass

    async def _force_join_prompt(self, update, context):
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup
        missing = await self._missing_force_channels(update, context)
        buttons = []
        channel_labels = {
            "@eraXarmy": ("📢 Join ERA X Army", "https://t.me/eraXarmy"),
            "@eraXearning": ("💰 Join ERA X Earning", "https://t.me/eraXearning"),
        }
        for channel in missing:
            label, url = channel_labels[channel]
            buttons.append([InlineKeyboardButton(label, url=url)])
        buttons.append([InlineKeyboardButton("✅ I Joined — Check Again", callback_data="check_join")])
        keyboard = InlineKeyboardMarkup(buttons)
        msg = update.message or update.callback_query.message
        await self._send_user(
            msg,
            f"{custom_emoji('🔒')} <b>Join the remaining channel(s)</b>\n\n"
            f"{custom_emoji('⚡')} Join only the channel button(s) shown below, then check again.",
            parse_mode="HTML", reply_markup=keyboard)

    # ---------- scan core ----------
    def _scan_apk_sync(self, apk_path, user_id, username=None):
        scanner = FirebaseScanner(apk_path)
        result = scanner.scan()
        scan_data = dict(result)
        scan_data['username'] = username
        scan_data['apk_name'] = Path(apk_path).name
        Database.save_scan(user_id, scan_data, firebase_session=self.firebase_session)
        return scan_data, scanner

    @staticmethod
    def _is_duplicate(scan_data):
        """Check whether the same DB URL + API key pair was already found."""
        db_url = PanelExchanger._db_url_of(scan_data)
        api_key = scan_data.get('api_key')
        for path in SCANS_DIR.glob('*.json'):
            try:
                with open(path, 'r') as f:
                    scans = json.load(f)
                if not isinstance(scans, list):
                    continue
                for s in scans:
                    if not s.get('found'):
                        continue
                    s_url = PanelExchanger._db_url_of(s)
                    s_key = s.get('api_key')
                    if (s_url and db_url and s_url.lower() == db_url.lower() and
                            s_key and api_key and s_key.lower() == api_key.lower()):
                        return True
            except Exception:
                continue
        return False

    def _format_era_result(self, data, apk_name=None):
        """Build a safe HTML result with Telegram custom-emoji entities."""
        lines = []
        dup = self._is_duplicate(data)
        if dup:
            lines.append(f"{custom_emoji('⚠')} <b>DUPLICATE — already in database</b>")
        lines.append(f"{custom_emoji('✅')} <b>Firebase Config Found!</b>\n━━━━━━━━━━━━━━━━━━")
        apk_label = html.escape(str(apk_name or data.get('apk_name', 'Sent file')))
        db_url = html.escape(str(PanelExchanger._db_url_of(data) or 'Not Found'))
        api_key = html.escape(str(data.get('api_key') or FIREBASE_ONLY_API_FALLBACK))
        lines.append(f"{custom_emoji('🚩')} <b>APK:</b> {apk_label}")
        # Credentials are intentionally shown only in this immediate scan result.
        lines.append(f"{custom_emoji('🌐')} <b>Firebase URL</b>\n{db_url}")
        lines.append(f"{custom_emoji('🎆')} <b>API Key</b>\n{api_key}")
        link = PanelExchanger.generate_encoded_link(data)
        if link:
            lines.append(f"{custom_emoji('🔗')} <b>Panel Link:</b>\n{html.escape(link)}")
        lines.append("━━━━━━━━━━━━━━━━━━")
        return "\n\n".join(lines)

    def _era_buttons(self, data):
        """User-safe keyboard: only the generated panel is exposed, never Firebase credentials."""
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup
        link = PanelExchanger.generate_encoded_link(data)
        row1 = [InlineKeyboardButton("🔗 Open Panel", url=link)] if link else []
        row2 = [InlineKeyboardButton("🏠 Main Menu", callback_data='main_menu')]
        return InlineKeyboardMarkup([row1, row2]) if row1 else InlineKeyboardMarkup([row2])

    # ---------- APK scan handler ----------
    async def handle_apk(self, update, context, document):
        user_id = update.effective_user.id
        if user_id in self.busy_users:
            return
        self.busy_users.add(user_id)
        try:
            if document.file_size > MAX_FILE_SIZE:
                await update.message.reply_text("❌ File too large (max 50MB).")
                return
            status = await self._send_user(update.message, "⬇️ Downloading APK...")
            try:
                user_file = await context.bot.get_file(document.file_id)
                file_name = document.file_name or "file.apk"
                if not file_name.lower().endswith('.apk'):
                    file_name += ".apk"
                dest = TEMP_DIR / file_name
                await user_file.download_to_drive(str(dest))

                await status.edit_text("🔍 Scanning APK for Firebase keys...")
                loop = asyncio.get_event_loop()
                scan_data, scanner = await loop.run_in_executor(
                    None, self._scan_apk_sync, dest, user_id,
                    update.effective_user.username)
                if scanner.temp_dir and scanner.temp_dir.exists():
                    shutil.rmtree(scanner.temp_dir, ignore_errors=True)
                dest.unlink(missing_ok=True)

                if scan_data.get('found'):
                    await self._clear_user_message(update.message)
                    await self._send_user(
                        update.message,
                        self._format_era_result(scan_data),
                        parse_mode='HTML',
                        reply_markup=self._era_buttons(scan_data))
                else:
                    await self._clear_user_message(update.message)
                    await self._send_user(
                        update.message,
                        "❌ No Firebase config found in this APK.")
                await status.delete()
            except Exception as e:
                logger.error(f"APK scan error: {e}")
                await status.edit_text(f"❌ Error scanning APK: {e}")
        finally:
            self.busy_users.discard(user_id)

    # ---------- bulk scan ----------
    async def handle_zip(self, update, context, document):
        user_id = update.effective_user.id
        if user_id in self.busy_users:
            return
        self.busy_users.add(user_id)
        try:
            if document.file_size > MAX_FILE_SIZE:
                await update.message.reply_text("❌ File too large (max 50MB).")
                return
            status = await update.message.reply_text("⬇️ Downloading ZIP...")
            try:
                user_file = await context.bot.get_file(document.file_id)
                dest = TEMP_DIR / f"bulk_{user_id}_{int(time.time())}.zip"
                await user_file.download_to_drive(str(dest))

                await status.edit_text("📦 Extracting ZIP...")
                extract_dir = TEMP_DIR / f"bulk_{user_id}_{int(time.time())}"
                extract_dir.mkdir(parents=True, exist_ok=True)
                with zipfile.ZipFile(dest, 'r') as zf:
                    names = [n for n in zf.namelist()
                             if n.lower().endswith('.apk')
                             and not n.startswith('__')]
                if not names:
                    await status.edit_text("❌ No APK files found in the ZIP.")
                    return
                if len(names) > MAX_BULK_SIZE:
                    names = names[:MAX_BULK_SIZE]
                    await status.edit_text(
                        f"⚠️ More than {MAX_BULK_SIZE} APKs found, "
                        f"scanning only the first {MAX_BULK_SIZE}."
                    )
                else:
                    await status.edit_text(
                        f"📦 Found {len(names)} APKs. Starting scan..."
                    )

                results = []
                with zipfile.ZipFile(dest, 'r') as zf:
                    for idx, name in enumerate(names, 1):
                        await status.edit_text(
                            f"🔍 Scanning {idx}/{len(names)}...")
                        try:
                            data = zf.read(name)
                        except Exception:
                            continue
                        apk_path = TEMP_DIR / f"bulk_{user_id}_{Path(name).name}"
                        with open(apk_path, 'wb') as f:
                            f.write(data)
                        loop = asyncio.get_event_loop()
                        scan_data, scanner = await loop.run_in_executor(
                            None, self._scan_apk_sync, apk_path, user_id,
                            update.effective_user.username)
                        if scanner.temp_dir and scanner.temp_dir.exists():
                            shutil.rmtree(scanner.temp_dir, ignore_errors=True)
                        apk_path.unlink(missing_ok=True)
                        if scan_data.get('found'):
                            results.append((name, scanner.get_panel_link()))
                        await asyncio.sleep(0.1)

                dest.unlink(missing_ok=True)

                if not results:
                    await status.edit_text(
                        "✅ *Bulk Scan Complete*\n\n"
                        "No Firebase keys found in any APK.",
                        parse_mode='Markdown'
                    )
                    return

                chunks = []
                current = "📦 *Bulk Scan Results*\n\n"
                count = 0
                for name, link in results:
                    line = f"✅ `{Path(name).name}`\n{link}\n\n"
                    if len(current) + len(line) > 3800:
                        chunks.append(current)
                        current = ""
                    current += line
                    count += 1
                if current:
                    chunks.append(current)

                for chunk in chunks:
                    await self._send(update.message, chunk, parse_mode='Markdown')
                    await asyncio.sleep(0.3)

                await status.edit_text(
                    f"✅ *Bulk scan complete*\n🔍 Scanned: {len(names)}\n"
                    f"✅ Found keys: {count}",
                    parse_mode='Markdown'
                )
            except Exception as e:
                logger.error(f"ZIP scan error: {e}")
                await status.edit_text(f"❌ Error during bulk scan: {e}")
        finally:
            self.busy_users.discard(user_id)

    # ---------- panel link handler ----------
    async def handle_panel_link(self, update, url, source_label=""):
        user_id = update.effective_user.id
        firebase_data = PanelExchanger.decode_panel_link(url)

        if not firebase_data or not firebase_data.get('found'):
            return False

        era_link = PanelExchanger.generate_era_panel(firebase_data)
        encoded_link = PanelExchanger.generate_encoded_link(firebase_data)

        panel_record = {
            'source_url': url[:500],
            'era_link': era_link,
            'api_key': firebase_data.get('api_key'),
            'database_url': PanelExchanger._db_url_of(firebase_data),
            'project_id': firebase_data.get('project_id'),
            'app_id': firebase_data.get('app_id'),
            'user_id': user_id,
        }
        # Persist asynchronously; never make the user wait for Firebase/network I/O.
        asyncio.create_task(asyncio.to_thread(
            Database.save_panel, user_id, panel_record, self.firebase_session))

        # Immediate conversion result includes credentials, while the saved Panel view remains private.
        # Duplicate lookup is local-only and is kept off the event loop.
        try:
            dup = await asyncio.wait_for(
                asyncio.to_thread(self._is_duplicate, firebase_data), timeout=1.5)
        except asyncio.TimeoutError:
            dup = False
        lines = []
        if dup:
            lines.append("⚠️ *DUPLICATE — already in database*")
        lines.append("✅ <b>Firebase Config Found!</b>\n━━━━━━━━━━━━━━━━━━\n")
        db_url = html.escape(str(PanelExchanger._db_url_of(firebase_data) or 'Not Found'))
        api_key = html.escape(str(firebase_data.get('api_key') or FIREBASE_ONLY_API_FALLBACK))
        lines.append(f"🌐 <b>Firebase URL</b>\n{db_url}\n")
        lines.append(f"🎆 <b>API Key</b>\n{api_key}\n")
        link = PanelExchanger.generate_encoded_link(firebase_data)
        if link:
            lines.append(f"🔗 <b>Panel Link:</b>\n{html.escape(link)}\n")
        lines.append("━━━━━━━━━━━━━━━━━━")
        await self._send_user(update.message, "\n".join(lines), parse_mode='HTML',
                              reply_markup=self._era_buttons(firebase_data))
        return True

    # ---------- message dispatcher (auto-detect) ----------
    async def on_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        # Register user and remove the previous user-facing bot message.
        user_id = update.effective_user.id
        user = update.effective_user
        await self._clear_user_message(update.message)
        if not await self._is_force_joined(update, context):
            await self._force_join_prompt(update, context)
            return
        state = _read_bot_state()
        if Database.is_banned(user_id) and not self._is_admin(user_id):
            await update.message.reply_text('🚫 Access suspended for this account.')
            return
        if not self._is_admin(user_id) and not state.get('enabled', True):
            await update.message.reply_text('⏸️ This service is temporarily offline. Please try again later.')
            return
        if not self._is_admin(user_id) and state.get('maintenance', False):
            await update.message.reply_text('🛠️ Maintenance mode is active. Your request will be available again soon.')
            return
        if self._is_admin(user_id) and context.user_data.get('admin_state') == 'broadcast':
            await self.broadcast_message(update, context)
            return
        user_data = Database.get_user(user_id)
        user_data['username'] = user.username
        user_data['first_name'] = user.first_name
        user_data['last_name'] = user.last_name
        await asyncio.to_thread(Database.save_user, user_id, user_data)
        if self.firebase_session:
            asyncio.create_task(asyncio.to_thread(FirebaseSync.save_user, self.firebase_session, user_id, user_data))

        # 1) Document: APK or ZIP
        doc = update.message.document
        if doc:
            lower_name = (doc.file_name or '').lower()
            if lower_name.endswith('.zip'):
                await self.handle_zip(update, context, doc)
                return
            # APK or any generic file -> try APK scan
            await self.handle_apk(update, context, doc)
            return

        # 2) Photo/media
        if update.message.photo or update.message.video or update.message.audio or update.message.voice:
            await update.message.reply_text(
                "📎 This bot scans APK files and converts panel links. "
                "Please send an APK file (.apk), a ZIP of APKs, or a panel link URL.")
            return

        # 3) Text: detect URL -> panel; else not understood
        text = update.message.text
        if not text or not text.strip():
            return

        text = text.strip()
        if text.startswith('/'):
            return  # commands handled separately

        # Extract URLs
        urls = PanelExchanger.URL_PATTERN.findall(text)
        if urls:
            converted = False
            for url in urls:
                url = url.rstrip('.,;!?)]}>')
                ok = await self.handle_panel_link(update, url)
                if ok:
                    converted = True
                    break
            if not converted:
                await update.message.reply_text(
                    "❌ *No Firebase data found in this link.*\n\n"
                    "Send a panel link containing Firebase keys (api_key, "
                    "project_id, app_id), an APK file, or a ZIP of APKs.",
                    parse_mode='Markdown')
            return

        # Raw API key in plain text?
        if PanelExchanger.API_KEY_STANDALONE_PATTERN.search(text):
            firebase_data = PanelExchanger._parse_firebase_data(text)
            if firebase_data.get('found'):
                await self.handle_panel_link(update, text)
                return

        # Unknown text
        await update.message.reply_text(
            "🤖 Send me:\n"
            "• an *APK file* (.apk) - I will scan it for Firebase keys\n"
            "• a *ZIP file* with multiple APKs - bulk scan\n"
            "• a *panel link* with Firebase keys - I will convert it\n\n"
            "Commands: /start /help /stats /keys /admin",
            parse_mode='Markdown')

    # ---------- commands ----------
    async def _main_menu(self, msg):
        """Show a button menu; direct APK, ZIP, URL, and Firebase text remain supported."""
        rows = [
            [InlineKeyboardButton('🔥 Scan APK', callback_data='scan_apk'),
             InlineKeyboardButton('🎁 Bulk Scan', callback_data='bulk_scan')],
            [             InlineKeyboardButton('📊 My Status', callback_data='my_status'),
             InlineKeyboardButton('🔗 Panel', callback_data='user_panels')],
        ]
        if getattr(msg, 'from_user', None) and msg.from_user.id in ADMIN_IDS:
            rows.append([InlineKeyboardButton('👑 Admin Panel', callback_data='admin_panel')])
        rows.append([InlineKeyboardButton('🏠 Main Menu', callback_data='main_menu')])
        keyboard = InlineKeyboardMarkup(rows)
        state = _read_bot_state()
        badge = '🟢 LIVE' if state.get('enabled', True) else '🔴 OFFLINE'
        await self._send_user(msg,
                         f"{custom_emoji('🚀')} <b>ERA X BOT</b>\n"
                         "╭────────────────────╮\n"
                         f"│  Service: <b>{html.escape(badge)}</b>       │\n"
                         "╰────────────────────╯\n\n"
                         "Welcome to your private analysis workspace.\n\n"
                         f"{custom_emoji('📦')} Drop an APK or ZIP here for automatic inspection.\n"
                         f"{custom_emoji('🔗')} Paste a supported Firebase/panel link for instant parsing.\n"
                         f"{custom_emoji('⚡')} No command is required; the console recognizes the input itself.\n\n"
                         "Choose a tool below, or simply send your file now.",
                         parse_mode='HTML', reply_markup=keyboard)

    async def start_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self._is_force_joined(update, context):
            await self._force_join_prompt(update, context)
            return
        await self._main_menu(update.message)

    async def on_callback(self, update, context):
        query = update.callback_query
        data = query.data
        await query.answer()
        if data == 'check_join':
            if await self._is_force_joined(update, context):
                await self._main_menu(query.message)
            else:
                await self._force_join_prompt(update, context)
            await self._delete_callback_message(query)
            return
        if not await self._is_force_joined(update, context):
            await self._force_join_prompt(update, context)
            await self._delete_callback_message(query)
            return
        if data == 'main_menu':
            await self._main_menu(query.message)
        elif data == 'scan_apk':
            await query.message.reply_text('📎 Send an APK file now. Direct file upload is also detected automatically.')
        elif data == 'bulk_scan':
            await query.message.reply_text('📦 Send a ZIP containing APK files, or send APKs one after another.')
        elif data == 'my_status':
            await self.stats_cmd(update, context)
        elif data == 'user_panels':
            await self.panels_cmd(update, context)
        elif data == 'firebase_keys':
            # Kept only for backward-compatible old messages; never expose credentials to users.
            await self.panels_cmd(update, context)
        elif data.startswith('firebase_keys_page:'):
            await self.keys_cmd(update, context, page=int(data.split(':', 1)[1]))
        elif data == 'admin_panel':
            await self.admin_cmd(update, context)
        elif data.startswith('admin_'):
            await self.admin_action(update, context, data)
        await self._delete_callback_message(query)

    async def help_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text(
            "📖 *ERA X Panel Bot - Help*\n\n"
            "No buttons, no commands needed - just send:\n\n"
            "📁 *APK file* (.apk)\n"
            "  ➜ scanned automatically for Firebase keys\n\n"
            "📦 *ZIP file* (up to 50 APKs)\n"
            "  ➜ bulk scanned automatically\n\n"
            "🔗 *Panel link* (with Firebase keys)\n"
            "  ➜ converted to ERA X link automatically\n\n"
            "Commands:\n"
            "/start - Start the bot\n"
            "/stats - Your statistics\n"
            "/keys - Your scanned Firebase keys\n"
            "/admin - Admin dashboard (admins only)",
            parse_mode='Markdown')

    async def stats_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        msg = update.message or update.callback_query.message
        user = Database.get_user(user_id)
        scans = Database.get_scans(user_id)
        keys = sum(1 for s in scans if s.get('found'))
        await msg.reply_text(
            "📊 *Your Statistics*\n\n"
            f"👤 User ID: `{user_id}`\n"
            f"🔍 Total Scans: {user.get('scans', 0)}\n"
            f"✅ Successful Scans: {keys}\n"
            f"🔄 Panels Exchanged: {user.get('panels', 0)}\n"
            f"📅 Joined: {user.get('joined', 'N/A')}\n"
            f"🕐 Last Scan: {user.get('last_scan', 'Never')}",
            parse_mode='Markdown')

    async def panels_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Show only the current user's generated panel links; never expose Firebase credentials."""
        msg = update.message or update.callback_query.message
        user_id = update.effective_user.id
        panels = Database.get_panels(user_id, limit=1000)
        links = []
        for item in panels:
            data = {
                'database_url': item.get('database_url'),
                'api_key': item.get('api_key') or FIREBASE_ONLY_API_FALLBACK,
                'project_id': item.get('project_id'),
                'app_id': item.get('app_id'),
            }
            link = PanelExchanger.generate_encoded_link(data)
            if link:
                links.append(link)
        if not links:
            await msg.reply_text("🔗 You have no panels yet.\n\nSend an APK or panel link to create your own panel.")
            return

        # User view intentionally contains only the generated URLs. Do not add
        # duplicate Open Panel buttons or Firebase credential details below them.
        chunks = []
        current = "🔗 Panel\n\n"
        for link in links:
            block = f"{link}\n\n"
            if len(current) + len(block) > 3900 and current.strip() != "🔗 Panel":
                chunks.append(current.rstrip())
                current = block
            else:
                current += block
        if current.strip():
            chunks.append(current.rstrip())

        for chunk in chunks:
            await msg.reply_text(chunk)

    async def keys_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE, page=0):
        # Legacy /keys is intentionally user-safe: normal users see panels, never credentials.
        if not self._is_admin(update.effective_user.id):
            return await self.panels_cmd(update, context)
        """Show only the current user's full Firebase records, 20 per page."""
        msg = update.message or update.callback_query.message
        user_id = update.effective_user.id
        keys = Database.get_firebase_keys(user_id=user_id)
        if not keys:
            await msg.reply_text("🔑 You have no Firebase records yet.\n\nSend an APK or panel link to create your own records!")
            return

        page_size = 20
        total_pages = max(1, (len(keys) + page_size - 1) // page_size)
        page = max(0, min(int(page), total_pages - 1))
        page_items = keys[page * page_size:(page + 1) * page_size]
        blocks = []
        for index, item in enumerate(page_items, start=page * page_size + 1):
            api_key = str(item.get('api_key') or FIREBASE_ONLY_API_FALLBACK)
            database_url = str(item.get('database_url') or item.get('firebase_url') or 'Not Found')
            project_id = str(item.get('project_id') or 'Not Found')
            blocks.append(
                f"🔑 Firebase #{index}\n"
                f"🌐 Firebase URL:\n{database_url}\n"
                f"🔐 API Key:\n{api_key}\n"
                f"🆔 Project ID:\n{project_id}"
            )

        header = f"🔑 Your Firebase Records — Page {page + 1}/{total_pages}\n\n"
        # Never truncate a URL/key. Split only between complete records if Telegram's limit is reached.
        chunks = []
        current = header
        for block in blocks:
            candidate = current + block + "\n\n"
            if len(candidate) > 3900 and current != header:
                chunks.append(current.rstrip())
                current = block + "\n\n"
            else:
                current = candidate
        if current.strip():
            chunks.append(current.rstrip())

        nav = []
        if page > 0:
            nav.append(InlineKeyboardButton("◀️ Previous", callback_data=f"firebase_keys_page:{page - 1}"))
        if page < total_pages - 1:
            nav.append(InlineKeyboardButton("Next ▶️", callback_data=f"firebase_keys_page:{page + 1}"))
        markup = InlineKeyboardMarkup([nav, [InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu")]]) if nav else InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu")]])
        for chunk in chunks[:-1]:
            await msg.reply_text(chunk)
        await msg.reply_text(chunks[-1], reply_markup=markup)

    # ---------- admin ----------
    async def admin_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        msg_target = update.message or update.callback_query.message
        if not self._is_admin(user_id):
            await msg_target.reply_text("⛔ You are not authorized!")
            return

        stats = Database.get_stats()
        users = Database.get_all_users()
        online_hours = 24
        active_users = sum(
            1 for u in users
            if u.get('last_scan') and
            (datetime.now() - datetime.fromisoformat(u['last_scan'])).total_seconds()
            < online_hours * 3600
        )

        msg = (f"👑 Admin Dashboard\n\n"
               f"📊 Total Users: {stats['users']}\n"
               f"🟢 Active (24h): {active_users}\n"
               f"🔍 Total Scans: {stats['scans']}\n"
               f"🔄 Total Panels: {stats['panels']}\n"
               f"🔑 Stored Keys: {stats['keys']}\n"
               f"🚫 Banned Users: {sum(1 for u in users if u.get('banned'))}\n"
               f"⏻ Bot: {'ON' if _read_bot_state().get('enabled', True) else 'OFF'} | "
               f"🛠️ Maintenance: {'ON' if _read_bot_state().get('maintenance', False) else 'OFF'}")

        users_sorted = sorted(users, key=lambda x: x.get('joined', ''), reverse=True)
        if users_sorted:
            msg += "\n\n👥 *Recent Users*\n"
            for u in users_sorted[:10]:
                uname = u.get('username') or u.get('first_name') or 'Unknown'
                msg += f"🆔 `{u.get('user_id')}` | @{uname} | Scans: {u.get('scans', 0)}\n"

        keys = Database.get_firebase_keys()
        if keys:
            msg += "\n\n🔑 *Recent Keys*\n"
            for k in keys[-5:]:
                msg += (f"🔑 `{k.get('api_key', 'N/A')[:25]}...` | "
                        f"🆔 `{k.get('project_id', 'N/A')}`\n")

        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton('👥 Users', callback_data='admin_users'), InlineKeyboardButton('🔑 Firebase', callback_data='admin_firebase')],
            [InlineKeyboardButton('📈 All Scans', callback_data='admin_scans'), InlineKeyboardButton('♻️ Duplicates', callback_data='admin_duplicates')],
            [InlineKeyboardButton('📣 Broadcast', callback_data='admin_broadcast')],
            [InlineKeyboardButton('🚫 Ban / Unban', callback_data='admin_ban_help')],
            [InlineKeyboardButton('🛠️ Maintenance', callback_data='admin_maintenance'), InlineKeyboardButton('⏻ Bot ON/OFF', callback_data='admin_toggle_bot')]
        ])
        await msg_target.reply_text(msg, reply_markup=keyboard)

    async def admin_action(self, update, context, action):
        query = update.callback_query
        if not self._is_admin(update.effective_user.id):
            await query.message.reply_text('⛔ Admin only.')
            return
        if action == 'admin_users':
            users = Database.get_all_users()
            lines = ['👥 Users']
            for u in users[:50]:
                state = 'BANNED' if u.get('banned') else 'active'
                lines.append(f"{u.get('user_id')} | @{u.get('username') or '-'} | {state} | scans {u.get('scans', 0)}")
            await query.message.reply_text('\n'.join(lines)[:4000])
        elif action == 'admin_firebase':
            keys = Database.get_firebase_keys()
            if not keys:
                await query.message.reply_text('🔑 No stored Firebase records.')
                return
            lines = ['🔑 Firebase records']
            for k in keys[:50]:
                lines.append(f"DB: {k.get('database_url') or '-'}\nAPI: {k.get('api_key') or FIREBASE_ONLY_API_FALLBACK}\nProject: {k.get('project_id') or '-'}\n")
            await query.message.reply_text('\n'.join(lines)[:4000])
        elif action == 'admin_scans':
            scans = Database.get_all_scans()[:30]
            lines = ['📈 Recent scans']
            for s in scans:
                lines.append(f"{s.get('timestamp','-')} | {s.get('user_id','-')} | {s.get('apk_name','-')} | {'FOUND' if s.get('found') else 'not found'}")
            await query.message.reply_text('\n'.join(lines)[:4000])
        elif action == 'admin_duplicates':
            seen, duplicates = set(), 0
            for s in Database.get_all_scans():
                key = (PanelExchanger._db_url_of(s), s.get('api_key') or FIREBASE_ONLY_API_FALLBACK)
                if key[0] and key in seen:
                    duplicates += 1
                elif key[0]:
                    seen.add(key)
            await query.message.reply_text(f'♻️ Duplicate records found: {duplicates}\nUse /dedupe to remove duplicate scan records.')
        elif action == 'admin_broadcast':
            context.user_data['admin_state'] = 'broadcast'
            await query.message.reply_text('📣 Send the next message/media to broadcast. It will be copied to all non-banned users. Send /cancel to abort.')
        elif action == 'admin_ban_help':
            await query.message.reply_text('🚫 Use /ban USER_ID or /unban USER_ID.\nUse /broadcast to send the next message to all non-banned users.')
        elif action == 'admin_maintenance':
            state = _read_bot_state()
            new_value = not state.get('maintenance', False)
            _write_bot_state(maintenance=new_value)
            await query.message.reply_text(f"🛠️ Maintenance mode {'ENABLED' if new_value else 'DISABLED'}.\nNormal users will {'see a maintenance notice' if new_value else 'be served normally'}.")
            await self.admin_cmd(update, context)
        elif action == 'admin_toggle_bot':
            state = _read_bot_state()
            new_value = not state.get('enabled', True)
            _write_bot_state(enabled=new_value)
            await query.message.reply_text(f"⏻ Bot is now {'ON' if new_value else 'OFF'}.\nAdmins can still access the control panel.")
            await self.admin_cmd(update, context)

    async def broadcast_message(self, update, context):
        if not self._is_admin(update.effective_user.id):
            return
        delivered = failed = 0
        for uid in Database.get_user_ids():
            if Database.is_banned(uid) or uid in ADMIN_IDS:
                continue
            try:
                await context.bot.copy_message(chat_id=uid, from_chat_id=update.effective_chat.id, message_id=update.message.message_id)
                delivered += 1
                await asyncio.sleep(0.05)
            except Exception:
                failed += 1
        context.user_data.pop('admin_state', None)
        await update.message.reply_text(f'📣 Broadcast complete. Delivered: {delivered}, failed: {failed}.')

    async def ban_cmd(self, update, context):
        if not self._is_admin(update.effective_user.id):
            await update.message.reply_text('⛔ Admin only.')
            return
        try:
            target = int(context.args[0])
            Database.set_banned(target, True)
            await update.message.reply_text(f'🚫 User {target} banned.')
        except (IndexError, ValueError):
            await update.message.reply_text('Usage: /ban USER_ID')

    async def unban_cmd(self, update, context):
        if not self._is_admin(update.effective_user.id):
            await update.message.reply_text('⛔ Admin only.')
            return
        try:
            target = int(context.args[0])
            Database.set_banned(target, False)
            await update.message.reply_text(f'✅ User {target} unbanned.')
        except (IndexError, ValueError):
            await update.message.reply_text('Usage: /unban USER_ID')

    async def broadcast_cmd(self, update, context):
        if not self._is_admin(update.effective_user.id):
            await update.message.reply_text('⛔ Admin only.')
            return
        context.user_data['admin_state'] = 'broadcast'
        await update.message.reply_text('📣 Send the next message/media to broadcast, or /cancel.')

    async def dedupe_cmd(self, update, context):
        if not self._is_admin(update.effective_user.id):
            await update.message.reply_text('⛔ Admin only.')
            return
        removed = 0
        for path in SCANS_DIR.glob('*.json'):
            try:
                scans = json.loads(path.read_text())
                if not isinstance(scans, list):
                    continue
                seen, kept = set(), []
                for scan in reversed(scans):
                    key = (PanelExchanger._db_url_of(scan), scan.get('api_key') or 'Ok')
                    if key[0] and key in seen:
                        removed += 1
                        continue
                    if key[0]:
                        seen.add(key)
                    kept.append(scan)
                path.write_text(json.dumps(list(reversed(kept)), indent=2))
            except Exception as exc:
                logger.warning('dedupe failed for %s: %s', path, exc)
        await update.message.reply_text(f'♻️ Duplicate cleanup complete. Removed: {removed}.')

    async def cancel_cmd(self, update, context):
        context.user_data.pop('admin_state', None)
        await update.message.reply_text('✅ Cancelled.')

    # ---------- runner ----------
    def start(self):
        telegram_request = HTTPXRequest(
            connection_pool_size=16,
            connect_timeout=20.0,
            read_timeout=45.0,
            write_timeout=45.0,
            pool_timeout=20.0,
        )
        self.application = (ApplicationBuilder()
                            .token(BOT_TOKEN)
                            .request(telegram_request)
                            .get_updates_request(telegram_request)
                            .build())

        self.application.add_handler(CommandHandler("start", self.start_cmd))
        self.application.add_handler(CommandHandler("help", self.help_cmd))
        self.application.add_handler(CallbackQueryHandler(self.on_callback, pattern='^(main_menu|check_join|scan_apk|bulk_scan|my_status|user_panels|firebase_keys|firebase_keys_page:[0-9]+|admin_panel|admin_users|admin_firebase|admin_scans|admin_duplicates|admin_broadcast|admin_ban_help|admin_maintenance|admin_toggle_bot|open_db|open_panel)$'))
        self.application.add_handler(CommandHandler("stats", self.stats_cmd))
        self.application.add_handler(CommandHandler("keys", self.keys_cmd))
        self.application.add_handler(CommandHandler("admin", self.admin_cmd))
        self.application.add_handler(CommandHandler("ban", self.ban_cmd))
        self.application.add_handler(CommandHandler("unban", self.unban_cmd))
        self.application.add_handler(CommandHandler("broadcast", self.broadcast_cmd))
        self.application.add_handler(CommandHandler("cancel", self.cancel_cmd))
        self.application.add_handler(CommandHandler("dedupe", self.dedupe_cmd))

        # Auto-detect: documents (APK/ZIP) and text (panel links)
        self.application.add_handler(
            MessageHandler(filters.Document.ALL & ~filters.COMMAND, self.on_message))
        self.application.add_handler(
            MessageHandler(filters.TEXT & ~filters.COMMAND, self.on_message))
        self.application.add_handler(
            MessageHandler((filters.PHOTO | filters.VIDEO | filters.AUDIO | filters.VOICE) & ~filters.COMMAND, self.on_message))

        logger.info("Starting ERA X Panel Bot (auto-detect mode)...")
        self.application.run_polling(drop_pending_updates=True)


# ==================== MAIN ====================
def main():
    # Load .env if present, then re-read config values
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass
    global BOT_TOKEN, ADMIN_IDS, FIREBASE_DB_URL
    BOT_TOKEN = os.environ.get("BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")
    ADMIN_IDS = [int(x) for x in os.environ.get("ADMIN_IDS", "").split(",") if x]
    FIREBASE_DB_URL = os.environ.get(
        "FIREBASE_DATABASE_URL", "https://era-ka-store-default-rtdb.firebaseio.com")

    if BOT_TOKEN == "YOUR_BOT_TOKEN_HERE":
        logger.error("BOT_TOKEN is not set! Set the BOT_TOKEN environment variable.")
        return

    firebase_session = req_lib.Session()
    try:
        r = firebase_session.get(FIREBASE_DB_URL + "/.json", timeout=10)
        logger.info(f"Firebase DB reachable: HTTP {r.status_code}")
    except Exception as e:
        logger.warning(f"Firebase DB not reachable: {e}")

    bot = Bot(firebase_session)
    try:
        bot.start()
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        raise


if __name__ == "__main__":
    main()
