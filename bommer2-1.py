import os
import json
import time
import secrets
import asyncio
import logging
from datetime import datetime, timezone

from aiohttp import web
from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)

# ============================================================
# KRUTIK CYBER EXPERT - API SELLING BOT
# ============================================================
# Required environment variables:
#   BOT_TOKEN   = Telegram bot token
#   ADMIN_IDS   = comma-separated Telegram user IDs
#
# Optional:
#   PORT        = Render port (default 10000)
#   DATA_FILE   = JSON database path (default api_selling_data.json)
#
# This bot is ONLY for API selling/access management.
# It does not contain SMS/Firebase/blast functionality.
# ============================================================

BRAND = "KRUTIK CYBER EXPERT"
VERSION = "1.0.0"

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_IDS_RAW = os.getenv("ADMIN_IDS", "").strip()
PORT = int(os.getenv("PORT", "10000"))
DATA_FILE = os.getenv("DATA_FILE", "api_selling_data.json")

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN environment variable is missing.")

def parse_admin_ids(value: str) -> set[int]:
    result = set()
    for item in value.split(","):
        item = item.strip()
        if not item:
            continue
        try:
            result.add(int(item))
        except ValueError:
            logging.warning("Invalid ADMIN_IDS value ignored: %s", item)
    return result

ADMIN_IDS = parse_admin_ids(ADMIN_IDS_RAW)

if not ADMIN_IDS:
    raise RuntimeError("ADMIN_IDS environment variable is missing or empty.")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)
log = logging.getLogger("KCE-API-Selling")

router = Router()
db_lock = asyncio.Lock()


# ============================================================
# DATABASE
# ============================================================

DEFAULT_DB = {
    "plans": [],
    "requests": {},
    "keys": {},
    "stats": {
        "total_requests": 0,
        "approved": 0,
        "rejected": 0,
    },
}


def load_db() -> dict:
    if not os.path.exists(DATA_FILE):
        return json.loads(json.dumps(DEFAULT_DB))

    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)

        if not isinstance(data, dict):
            raise ValueError("Database root is not an object.")

        for key, value in DEFAULT_DB.items():
            if key not in data:
                data[key] = json.loads(json.dumps(value))

        return data
    except Exception as e:
        log.error("Database load failed: %s", e)
        return json.loads(json.dumps(DEFAULT_DB))


def save_db(data: dict) -> None:
    directory = os.path.dirname(os.path.abspath(DATA_FILE))
    os.makedirs(directory, exist_ok=True)

    temp_file = DATA_FILE + ".tmp"
    with open(temp_file, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    os.replace(temp_file, DATA_FILE)


def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


def now_ts() -> int:
    return int(time.time())


def fmt_date(ts: int | float | None) -> str:
    if not ts:
        return "-"
    return datetime.fromtimestamp(
        int(ts), tz=timezone.utc
    ).strftime("%d %b %Y, %H:%M UTC")


def escape_html(text: str) -> str:
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def generate_id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(5)}"


def generate_api_key() -> str:
    return "KCE-" + secrets.token_hex(20).upper()


def get_plan(db: dict, plan_id: str) -> dict | None:
    for plan in db.get("plans", []):
        if plan.get("id") == plan_id:
            return plan
    return None


def get_active_keys_for_user(db: dict, user_id: int) -> list[dict]:
    current = now_ts()
    result = []

    for key_data in db.get("keys", {}).values():
        if key_data.get("user_id") != user_id:
            continue

        if key_data.get("status") == "active" and key_data.get("expires_at", 0) <= current:
            key_data["status"] = "expired"

        if key_data.get("status") == "active":
            result.append(key_data)

    return result


# ============================================================
# KEYBOARDS
# ============================================================

def user_home_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📦 API Plans",
                    callback_data="user:plans",
                )
            ],
            [
                InlineKeyboardButton(
                    text="🔑 My API",
                    callback_data="user:myapi",
                ),
                InlineKeyboardButton(
                    text="📊 Usage",
                    callback_data="user:usage",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="📨 My Requests",
                    callback_data="user:requests",
                )
            ],
        ]
    )


def admin_home_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📋 Pending Requests",
                    callback_data="admin:pending",
                )
            ],
            [
                InlineKeyboardButton(
                    text="📦 API Plans",
                    callback_data="admin:plans",
                ),
                InlineKeyboardButton(
                    text="➕ Add Plan",
                    callback_data="admin:addplan",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="🔑 Active Keys",
                    callback_data="admin:keys",
                ),
                InlineKeyboardButton(
                    text="📊 Statistics",
                    callback_data="admin:stats",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="🏠 User Menu",
                    callback_data="user:home",
                )
            ],
        ]
    )


def back_user_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🔙 Back",
                    callback_data="user:home",
                )
            ]
        ]
    )


def back_admin_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🔙 Admin Panel",
                    callback_data="admin:home",
                )
            ]
        ]
    )


# ============================================================
# FSM
# ============================================================

class AddPlanState(StatesGroup):
    name = State()
    price = State()
    limit = State()
    duration = State()


class EditPlanState(StatesGroup):
    choose_field = State()
    value = State()


class RequestState(StatesGroup):
    note = State()


# ============================================================
# TEXT HELPERS
# ============================================================

def plan_text(plan: dict) -> str:
    return (
        f"📦 <b>{escape_html(plan.get('name', 'API Plan'))}</b>\n"
        f"💰 Price: <b>₹{plan.get('price', 0)}</b>\n"
        f"📊 Limit: <b>{plan.get('limit', 0):,}</b> requests\n"
        f"📅 Validity: <b>{plan.get('duration_days', 0)} days</b>"
    )


def status_icon(status: str) -> str:
    return {
        "pending": "⏳",
        "approved": "✅",
        "rejected": "❌",
        "active": "🟢",
        "expired": "⌛",
    }.get(status, "•")


# ============================================================
# START / COMMANDS
# ============================================================

@router.message(CommandStart())
async def start(message: Message, state: FSMContext):
    await state.clear()

    text = (
        f"🔑 <b>{BRAND}</b>\n\n"
        f"<b>API SELLING BOT</b>\n\n"
        f"Buy/request API access, check your API key and view usage.\n\n"
        f"Choose an option below."
    )

    await message.answer(
        text,
        reply_markup=user_home_kb(),
    )


@router.message(Command("admin"))
async def admin_command(message: Message, state: FSMContext):
    await state.clear()

    if not is_admin(message.from_user.id):
        await message.answer("🚫 <b>Admin Only</b>")
        return

    await message.answer(
        f"👑 <b>{BRAND}</b>\n\n<b>API SELLING ADMIN PANEL</b>",
        reply_markup=admin_home_kb(),
    )


# ============================================================
# USER HOME
# ============================================================

@router.callback_query(F.data == "user:home")
async def user_home(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.answer()

    await callback.message.edit_text(
        f"🔑 <b>{BRAND}</b>\n\n"
        f"<b>API SELLING BOT</b>\n\n"
        f"Manage your API access from the buttons below.",
        reply_markup=user_home_kb(),
    )


# ============================================================
# USER: PLANS
# ============================================================

@router.callback_query(F.data == "user:plans")
async def user_plans(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.answer()

    db = load_db()
    plans = db.get("plans", [])

    if not plans:
        await callback.message.edit_text(
            f"📦 <b>{BRAND}</b>\n\n"
            f"❌ No API plans are available right now.",
            reply_markup=back_user_kb(),
        )
        return

    rows = []

    for plan in plans:
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"🛒 {plan.get('name', 'API Plan')}",
                    callback_data=f"user:plan:{plan['id']}",
                )
            ]
        )

    rows.append(
        [
            InlineKeyboardButton(
                text="🔙 Back",
                callback_data="user:home",
            )
        ]
    )

    text = f"📦 <b>{BRAND}</b>\n\n<b>AVAILABLE API PLANS</b>\n\n"

    for plan in plans:
        text += plan_text(plan) + "\n\n"

    await callback.message.edit_text(
        text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )


@router.callback_query(F.data.startswith("user:plan:"))
async def user_plan_details(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.answer()

    plan_id = callback.data.split("user:plan:", 1)[1]
    db = load_db()
    plan = get_plan(db, plan_id)

    if not plan:
        await callback.message.edit_text(
            "❌ <b>Plan not found.</b>\n\n"
            "Please open API Plans again.",
            reply_markup=back_user_kb(),
        )
        return

    markup = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🛒 Request This API",
                    callback_data=f"user:request:{plan_id}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="🔙 Back to Plans",
                    callback_data="user:plans",
                )
            ],
        ]
    )

    await callback.message.edit_text(
        f"🔑 <b>{BRAND}</b>\n\n"
        f"{plan_text(plan)}\n\n"
        f"To purchase/request this API, send an access request to the admin.",
        reply_markup=markup,
    )


# ============================================================
# USER: REQUEST API
# ============================================================

@router.callback_query(F.data.startswith("user:request:"))
async def user_request_start(callback: CallbackQuery, state: FSMContext):
    plan_id = callback.data.split("user:request:", 1)[1]
    db = load_db()
    plan = get_plan(db, plan_id)

    if not plan:
        await callback.answer(
            "Plan not found. Please refresh the plans.",
            show_alert=True,
        )
        return

    user_id = callback.from_user.id

    for req in db.get("requests", {}).values():
        if (
            req.get("user_id") == user_id
            and req.get("plan_id") == plan_id
            and req.get("status") == "pending"
        ):
            await callback.answer(
                "A request for this plan is already pending.",
                show_alert=True,
            )
            return

    await state.update_data(plan_id=plan_id)
    await state.set_state(RequestState.note)
    await callback.answer()

    await callback.message.edit_text(
        f"📨 <b>{BRAND}</b>\n\n"
        f"{plan_text(plan)}\n\n"
        f"📝 Send a short note for the admin.\n"
        f"<i>Example: Personal project / Business use</i>\n\n"
        f"Send <b>-</b> if you don't want to add a note.",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="❌ Cancel",
                        callback_data="user:plans",
                    )
                ]
            ]
        ),
    )


@router.message(RequestState.note)
async def user_request_note(message: Message, state: FSMContext):
    db = load_db()
    data = await state.get_data()
    plan_id = data.get("plan_id")
    plan = get_plan(db, plan_id)

    if not plan:
        await state.clear()
        await message.answer(
            "❌ This plan no longer exists.",
            reply_markup=user_home_kb(),
        )
        return

    note = (message.text or "-").strip()[:500]

    request_id = generate_id("REQ")

    req = {
        "id": request_id,
        "user_id": message.from_user.id,
        "username": message.from_user.username or "",
        "name": message.from_user.full_name or "Unknown",
        "plan_id": plan["id"],
        "plan_name": plan["name"],
        "price": plan["price"],
        "limit": plan["limit"],
        "duration_days": plan["duration_days"],
        "note": note,
        "status": "pending",
        "created_at": now_ts(),
    }

    db.setdefault("requests", {})[request_id] = req
    db.setdefault("stats", {}).setdefault("total_requests", 0)
    db["stats"]["total_requests"] += 1
    save_db(db)

    await state.clear()

    await message.answer(
        f"✅ <b>{BRAND}</b>\n\n"
        f"Your API access request has been submitted.\n\n"
        f"🆔 Request ID: <code>{request_id}</code>\n"
        f"📦 Plan: <b>{escape_html(plan['name'])}</b>\n"
        f"💰 Price: <b>₹{plan['price']}</b>\n"
        f"📊 Limit: <b>{plan['limit']:,}</b>\n"
        f"📅 Validity: <b>{plan['duration_days']} days</b>\n"
        f"📌 Status: <b>⏳ PENDING</b>\n\n"
        f"An admin will review your request.",
        reply_markup=user_home_kb(),
    )

    admin_text = (
        f"📨 <b>{BRAND}</b>\n\n"
        f"<b>NEW API ACCESS REQUEST</b>\n\n"
        f"👤 User: <b>{escape_html(req['name'])}</b>\n"
        f"🆔 User ID: <code>{req['user_id']}</code>\n"
        f"🔗 Username: @{escape_html(req['username']) if req['username'] else '-'}\n"
        f"🆔 Request: <code>{request_id}</code>\n\n"
        f"📦 Plan: <b>{escape_html(req['plan_name'])}</b>\n"
        f"💰 Price: <b>₹{req['price']}</b>\n"
        f"📊 Limit: <b>{req['limit']:,}</b>\n"
        f"📅 Validity: <b>{req['duration_days']} days</b>\n"
        f"📝 Note: {escape_html(note)}"
    )

    admin_markup = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ APPROVE",
                    callback_data=f"admin:approve:{request_id}",
                ),
                InlineKeyboardButton(
                    text="❌ REJECT",
                    callback_data=f"admin:reject:{request_id}",
                ),
            ]
        ]
    )

    bot = message.bot

    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(
                admin_id,
                admin_text,
                reply_markup=admin_markup,
            )
        except Exception as e:
            log.warning("Could not notify admin %s: %s", admin_id, e)


# ============================================================
# USER: REQUESTS
# ============================================================

@router.callback_query(F.data == "user:requests")
async def user_requests(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.answer()

    db = load_db()
    user_id = callback.from_user.id

    requests = [
        r for r in db.get("requests", {}).values()
        if r.get("user_id") == user_id
    ]

    requests.sort(key=lambda x: x.get("created_at", 0), reverse=True)

    if not requests:
        text = (
            f"📨 <b>{BRAND}</b>\n\n"
            f"No API access requests found."
        )
    else:
        text = f"📨 <b>{BRAND}</b>\n\n<b>MY REQUESTS</b>\n\n"

        for req in requests[:15]:
            text += (
                f"{status_icon(req.get('status'))} "
                f"<b>{escape_html(req.get('plan_name', '-'))}</b>\n"
                f"🆔 <code>{req.get('id')}</code>\n"
                f"📌 {req.get('status', '-').upper()}\n"
                f"🕒 {fmt_date(req.get('created_at'))}\n\n"
            )

    await callback.message.edit_text(
        text,
        reply_markup=back_user_kb(),
    )


# ============================================================
# USER: MY API
# ============================================================

@router.callback_query(F.data == "user:myapi")
async def user_my_api(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.answer()

    db = load_db()
    keys = get_active_keys_for_user(db, callback.from_user.id)
    save_db(db)

    if not keys:
        await callback.message.edit_text(
            f"🔑 <b>{BRAND}</b>\n\n"
            f"You don't have an active API key.",
            reply_markup=back_user_kb(),
        )
        return

    text = f"🔑 <b>{BRAND}</b>\n\n<b>MY ACTIVE API KEYS</b>\n\n"

    for item in keys:
        used = int(item.get("used", 0))
        limit = int(item.get("limit", 0))
        remaining = max(0, limit - used)

        text += (
            f"🟢 <b>{escape_html(item.get('plan_name', '-'))}</b>\n"
            f"🔑 <code>{item.get('key')}</code>\n"
            f"📊 Used: <b>{used:,}</b> / <b>{limit:,}</b>\n"
            f"📈 Remaining: <b>{remaining:,}</b>\n"
            f"📅 Expires: <b>{fmt_date(item.get('expires_at'))}</b>\n"
            f"📌 Status: <b>{item.get('status', '-').upper()}</b>\n\n"
        )

    await callback.message.edit_text(
        text,
        reply_markup=back_user_kb(),
    )


# ============================================================
# USER: USAGE
# ============================================================

@router.callback_query(F.data == "user:usage")
async def user_usage(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.answer()

    db = load_db()
    keys = get_active_keys_for_user(db, callback.from_user.id)
    save_db(db)

    if not keys:
        await callback.message.edit_text(
            f"📊 <b>{BRAND}</b>\n\n"
            f"No active API key found.",
            reply_markup=back_user_kb(),
        )
        return

    text = f"📊 <b>{BRAND}</b>\n\n<b>API USAGE</b>\n\n"

    for item in keys:
        used = int(item.get("used", 0))
        limit = int(item.get("limit", 0))
        remaining = max(0, limit - used)

        text += (
            f"🔑 <code>{item.get('key')}</code>\n"
            f"📦 {escape_html(item.get('plan_name', '-'))}\n"
            f"📈 Used: <b>{used:,}</b>\n"
            f"📊 Limit: <b>{limit:,}</b>\n"
            f"🟢 Remaining: <b>{remaining:,}</b>\n"
            f"📅 Expiry: <b>{fmt_date(item.get('expires_at'))}</b>\n\n"
        )

    await callback.message.edit_text(
        text,
        reply_markup=back_user_kb(),
    )


# ============================================================
# ADMIN HOME
# ============================================================

@router.callback_query(F.data == "admin:home")
async def admin_home(callback: CallbackQuery, state: FSMContext):
    await state.clear()

    if not is_admin(callback.from_user.id):
        await callback.answer("🚫 Admin Only!", show_alert=True)
        return

    await callback.answer()

    await callback.message.edit_text(
        f"👑 <b>{BRAND}</b>\n\n"
        f"<b>API SELLING ADMIN PANEL</b>",
        reply_markup=admin_home_kb(),
    )


# ============================================================
# ADMIN: PENDING
# ============================================================

@router.callback_query(F.data == "admin:pending")
async def admin_pending(callback: CallbackQuery, state: FSMContext):
    await state.clear()

    if not is_admin(callback.from_user.id):
        await callback.answer("🚫 Admin Only!", show_alert=True)
        return

    await callback.answer()

    db = load_db()

    pending = [
        r for r in db.get("requests", {}).values()
        if r.get("status") == "pending"
    ]

    pending.sort(key=lambda x: x.get("created_at", 0))

    if not pending:
        await callback.message.edit_text(
            f"📋 <b>{BRAND}</b>\n\n"
            f"✅ No pending API requests.",
            reply_markup=back_admin_kb(),
        )
        return

    rows = []

    for req in pending[:30]:
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"📨 {req.get('plan_name', 'API')} — {req.get('name', 'User')[:18]}",
                    callback_data=f"admin:view:{req['id']}",
                )
            ]
        )

    rows.append(
        [
            InlineKeyboardButton(
                text="🔙 Admin Panel",
                callback_data="admin:home",
            )
        ]
    )

    text = (
        f"📋 <b>{BRAND}</b>\n\n"
        f"<b>PENDING REQUESTS: {len(pending)}</b>\n\n"
        f"Select a request:"
    )

    await callback.message.edit_text(
        text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )


@router.callback_query(F.data.startswith("admin:view:"))
async def admin_view_request(callback: CallbackQuery, state: FSMContext):
    await state.clear()

    if not is_admin(callback.from_user.id):
        await callback.answer("🚫 Admin Only!", show_alert=True)
        return

    request_id = callback.data.split("admin:view:", 1)[1]
    db = load_db()
    req = db.get("requests", {}).get(request_id)

    if not req:
        await callback.answer("Request not found.", show_alert=True)
        return

    await callback.answer()

    text = (
        f"📨 <b>{BRAND}</b>\n\n"
        f"<b>API ACCESS REQUEST</b>\n\n"
        f"👤 Name: <b>{escape_html(req.get('name', '-'))}</b>\n"
        f"🆔 User ID: <code>{req.get('user_id')}</code>\n"
        f"🔗 Username: @{escape_html(req.get('username')) if req.get('username') else '-'}\n"
        f"🆔 Request ID: <code>{req.get('id')}</code>\n\n"
        f"📦 Plan: <b>{escape_html(req.get('plan_name', '-'))}</b>\n"
        f"💰 Price: <b>₹{req.get('price', 0)}</b>\n"
        f"📊 Limit: <b>{req.get('limit', 0):,}</b>\n"
        f"📅 Validity: <b>{req.get('duration_days', 0)} days</b>\n"
        f"📝 Note: {escape_html(req.get('note', '-'))}\n"
        f"📌 Status: <b>{req.get('status', '-').upper()}</b>"
    )

    rows = []

    if req.get("status") == "pending":
        rows.append(
            [
                InlineKeyboardButton(
                    text="✅ APPROVE",
                    callback_data=f"admin:approve:{request_id}",
                ),
                InlineKeyboardButton(
                    text="❌ REJECT",
                    callback_data=f"admin:reject:{request_id}",
                ),
            ]
        )

    rows.append(
        [
            InlineKeyboardButton(
                text="🔙 Pending",
                callback_data="admin:pending",
            )
        ]
    )

    await callback.message.edit_text(
        text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )


# ============================================================
# ADMIN: APPROVE
# ============================================================

@router.callback_query(F.data.startswith("admin:approve:"))
async def admin_approve(callback: CallbackQuery, state: FSMContext):
    await state.clear()

    if not is_admin(callback.from_user.id):
        await callback.answer("🚫 Admin Only!", show_alert=True)
        return

    request_id = callback.data.split("admin:approve:", 1)[1]

    db = load_db()
    req = db.get("requests", {}).get(request_id)

    if not req:
        await callback.answer("Request not found.", show_alert=True)
        return

    if req.get("status") != "pending":
        await callback.answer("This request is already processed.", show_alert=True)
        return

    plan = get_plan(db, req.get("plan_id"))

    if not plan:
        await callback.answer(
            "The plan no longer exists. Recreate the plan first.",
            show_alert=True,
        )
        return

    key = generate_api_key()
    created = now_ts()
    expires = created + int(plan["duration_days"]) * 86400

    key_record = {
        "key": key,
        "user_id": req["user_id"],
        "plan_id": plan["id"],
        "plan_name": plan["name"],
        "limit": int(plan["limit"]),
        "used": 0,
        "status": "active",
        "created_at": created,
        "expires_at": expires,
        "approved_by": callback.from_user.id,
        "request_id": request_id,
    }

    db.setdefault("keys", {})[key] = key_record

    req["status"] = "approved"
    req["approved_by"] = callback.from_user.id
    req["approved_at"] = created
    req["api_key"] = key

    db.setdefault("stats", {}).setdefault("approved", 0)
    db["stats"]["approved"] += 1

    save_db(db)

    await callback.answer("✅ API approved and key generated!", show_alert=True)

    try:
        await callback.bot.send_message(
            req["user_id"],
            f"🎉 <b>{BRAND}</b>\n\n"
            f"✅ <b>Your API access has been approved!</b>\n\n"
            f"📦 Plan: <b>{escape_html(plan['name'])}</b>\n"
            f"🔑 API Key:\n<code>{key}</code>\n\n"
            f"📊 Limit: <b>{plan['limit']:,}</b> requests\n"
            f"📅 Expires: <b>{fmt_date(expires)}</b>\n\n"
            f"🌐 Verify endpoint:\n"
            f"<code>/api/verify?api_key=YOUR_KEY</code>",
        )
    except Exception as e:
        log.warning("Could not notify approved user %s: %s", req["user_id"], e)

    await admin_pending(callback, state)


# ============================================================
# ADMIN: REJECT
# ============================================================

@router.callback_query(F.data.startswith("admin:reject:"))
async def admin_reject(callback: CallbackQuery, state: FSMContext):
    await state.clear()

    if not is_admin(callback.from_user.id):
        await callback.answer("🚫 Admin Only!", show_alert=True)
        return

    request_id = callback.data.split("admin:reject:", 1)[1]

    db = load_db()
    req = db.get("requests", {}).get(request_id)

    if not req:
        await callback.answer("Request not found.", show_alert=True)
        return

    if req.get("status") != "pending":
        await callback.answer("This request is already processed.", show_alert=True)
        return

    req["status"] = "rejected"
    req["rejected_by"] = callback.from_user.id
    req["rejected_at"] = now_ts()

    db.setdefault("stats", {}).setdefault("rejected", 0)
    db["stats"]["rejected"] += 1

    save_db(db)

    await callback.answer("❌ Request rejected.", show_alert=True)

    try:
        await callback.bot.send_message(
            req["user_id"],
            f"❌ <b>{BRAND}</b>\n\n"
            f"Your API access request has been rejected.\n\n"
            f"🆔 Request: <code>{request_id}</code>\n"
            f"📦 Plan: <b>{escape_html(req.get('plan_name', '-'))}</b>",
        )
    except Exception as e:
        log.warning("Could not notify rejected user %s: %s", req["user_id"], e)

    await admin_pending(callback, state)


# ============================================================
# ADMIN: PLANS
# ============================================================

@router.callback_query(F.data == "admin:plans")
async def admin_plans(callback: CallbackQuery, state: FSMContext):
    await state.clear()

    if not is_admin(callback.from_user.id):
        await callback.answer("🚫 Admin Only!", show_alert=True)
        return

    await callback.answer()

    db = load_db()
    plans = db.get("plans", [])

    if not plans:
        text = f"📦 <b>{BRAND}</b>\n\nNo API plans created yet."
    else:
        text = f"📦 <b>{BRAND}</b>\n\n<b>API PLANS</b>\n\n"

        for plan in plans:
            text += plan_text(plan) + f"\n🆔 <code>{plan['id']}</code>\n\n"

    rows = [
        [
            InlineKeyboardButton(
                text="➕ Add Plan",
                callback_data="admin:addplan",
            )
        ]
    ]

    for plan in plans:
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"✏️ {plan['name'][:22]}",
                    callback_data=f"admin:edit:{plan['id']}",
                ),
                InlineKeyboardButton(
                    text="🗑",
                    callback_data=f"admin:delete:{plan['id']}",
                ),
            ]
        )

    rows.append(
        [
            InlineKeyboardButton(
                text="🔙 Admin Panel",
                callback_data="admin:home",
            )
        ]
    )

    await callback.message.edit_text(
        text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )


# ============================================================
# ADMIN: ADD PLAN
# ============================================================

@router.callback_query(F.data == "admin:addplan")
async def admin_add_plan(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("🚫 Admin Only!", show_alert=True)
        return

    await state.clear()
    await state.set_state(AddPlanState.name)
    await callback.answer()

    await callback.message.edit_text(
        f"➕ <b>{BRAND}</b>\n\n"
        f"<b>ADD API PLAN</b>\n\n"
        f"1️⃣ Send plan name.\n"
        f"<i>Example: Basic API</i>",
        reply_markup=back_admin_kb(),
    )


@router.message(AddPlanState.name)
async def add_plan_name(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await state.clear()
        return

    name = (message.text or "").strip()

    if not name or len(name) > 50:
        await message.answer("❌ Plan name must be between 1 and 50 characters.")
        return

    await state.update_data(name=name)
    await state.set_state(AddPlanState.price)

    await message.answer(
        "2️⃣ Send price in INR.\n"
        "<i>Example: 99</i>"
    )


@router.message(AddPlanState.price)
async def add_plan_price(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await state.clear()
        return

    try:
        price = float((message.text or "").strip())
        if price < 0:
            raise ValueError
    except ValueError:
        await message.answer("❌ Invalid price. Example: 99")
        return

    await state.update_data(price=price)
    await state.set_state(AddPlanState.limit)

    await message.answer(
        "3️⃣ Send API request limit.\n"
        "<i>Example: 1000</i>"
    )


@router.message(AddPlanState.limit)
async def add_plan_limit(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await state.clear()
        return

    try:
        limit = int((message.text or "").strip())
        if limit <= 0:
            raise ValueError
    except ValueError:
        await message.answer("❌ Invalid limit. Example: 1000")
        return

    await state.update_data(limit=limit)
    await state.set_state(AddPlanState.duration)

    await message.answer(
        "4️⃣ Send validity in days.\n"
        "<i>Example: 30</i>"
    )


@router.message(AddPlanState.duration)
async def add_plan_duration(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await state.clear()
        return

    try:
        duration = int((message.text or "").strip())
        if duration <= 0:
            raise ValueError
    except ValueError:
        await message.answer("❌ Invalid duration. Example: 30")
        return

    data = await state.get_data()

    plan = {
        "id": generate_id("PLAN"),
        "name": data["name"],
        "price": data["price"],
        "limit": data["limit"],
        "duration_days": duration,
        "created_at": now_ts(),
    }

    db = load_db()
    db.setdefault("plans", []).append(plan)
    save_db(db)

    await state.clear()

    await message.answer(
        f"✅ <b>{BRAND}</b>\n\n"
        f"<b>API PLAN CREATED</b>\n\n"
        f"{plan_text(plan)}",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="📦 API Plans",
                        callback_data="admin:plans",
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="👑 Admin Panel",
                        callback_data="admin:home",
                    )
                ],
            ]
        ),
    )


# ============================================================
# ADMIN: EDIT PLAN
# ============================================================

@router.callback_query(F.data.startswith("admin:edit:"))
async def admin_edit_plan(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("🚫 Admin Only!", show_alert=True)
        return

    plan_id = callback.data.split("admin:edit:", 1)[1]
    db = load_db()
    plan = get_plan(db, plan_id)

    if not plan:
        await callback.answer("Plan not found.", show_alert=True)
        return

    await state.clear()
    await callback.answer()

    await callback.message.edit_text(
        f"✏️ <b>{BRAND}</b>\n\n"
        f"{plan_text(plan)}\n\n"
        f"Choose what you want to edit:",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="📝 Name",
                        callback_data=f"admin:editfield:name:{plan_id}",
                    ),
                    InlineKeyboardButton(
                        text="💰 Price",
                        callback_data=f"admin:editfield:price:{plan_id}",
                    ),
                ],
                [
                    InlineKeyboardButton(
                        text="📊 Limit",
                        callback_data=f"admin:editfield:limit:{plan_id}",
                    ),
                    InlineKeyboardButton(
                        text="📅 Duration",
                        callback_data=f"admin:editfield:duration:{plan_id}",
                    ),
                ],
                [
                    InlineKeyboardButton(
                        text="🔙 API Plans",
                        callback_data="admin:plans",
                    )
                ],
            ]
        ),
    )


@router.callback_query(F.data.startswith("admin:editfield:"))
async def admin_edit_field(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("🚫 Admin Only!", show_alert=True)
        return

    parts = callback.data.split(":")
    # admin:editfield:<field>:<plan_id>
    if len(parts) < 4:
        await callback.answer("Invalid edit action.", show_alert=True)
        return

    field = parts[2]
    plan_id = parts[3]

    db = load_db()
    plan = get_plan(db, plan_id)

    if not plan:
        await callback.answer("Plan not found.", show_alert=True)
        return

    await state.update_data(plan_id=plan_id, field=field)
    await state.set_state(EditPlanState.value)
    await callback.answer()

    labels = {
        "name": "plan name",
        "price": "price in INR",
        "limit": "request limit",
        "duration": "validity in days",
    }

    examples = {
        "name": "Basic API",
        "price": "99",
        "limit": "1000",
        "duration": "30",
    }

    await callback.message.edit_text(
        f"✏️ <b>{BRAND}</b>\n\n"
        f"Send new <b>{labels.get(field, field)}</b>.\n"
        f"<i>Example: {examples.get(field, '')}</i>",
        reply_markup=back_admin_kb(),
    )


@router.message(EditPlanState.value)
async def admin_edit_value(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await state.clear()
        return

    data = await state.get_data()
    plan_id = data.get("plan_id")
    field = data.get("field")

    db = load_db()
    plan = get_plan(db, plan_id)

    if not plan:
        await state.clear()
        await message.answer("❌ Plan not found.", reply_markup=admin_home_kb())
        return

    raw = (message.text or "").strip()

    try:
        if field == "name":
            if not raw or len(raw) > 50:
                raise ValueError
            plan["name"] = raw

        elif field == "price":
            value = float(raw)
            if value < 0:
                raise ValueError
            plan["price"] = value

        elif field == "limit":
            value = int(raw)
            if value <= 0:
                raise ValueError
            plan["limit"] = value

        elif field == "duration":
            value = int(raw)
            if value <= 0:
                raise ValueError
            plan["duration_days"] = value

        else:
            raise ValueError

    except ValueError:
        await message.answer("❌ Invalid value. Please try again.")
        return

    plan["updated_at"] = now_ts()
    save_db(db)
    await state.clear()

    await message.answer(
        f"✅ <b>{BRAND}</b>\n\n"
        f"Plan updated successfully.\n\n"
        f"{plan_text(plan)}",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="📦 API Plans",
                        callback_data="admin:plans",
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="👑 Admin Panel",
                        callback_data="admin:home",
                    )
                ],
            ]
        ),
    )


# ============================================================
# ADMIN: DELETE PLAN
# ============================================================

@router.callback_query(F.data.startswith("admin:delete:"))
async def admin_delete_plan_confirm(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("🚫 Admin Only!", show_alert=True)
        return

    plan_id = callback.data.split("admin:delete:", 1)[1]
    db = load_db()
    plan = get_plan(db, plan_id)

    if not plan:
        await callback.answer("Plan not found.", show_alert=True)
        return

    await callback.answer()

    await callback.message.edit_text(
        f"🗑 <b>{BRAND}</b>\n\n"
        f"Delete this plan?\n\n"
        f"{plan_text(plan)}\n\n"
        f"<b>Note:</b> Existing approved API keys are not deleted.",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="🗑 YES, DELETE",
                        callback_data=f"admin:deleteconfirm:{plan_id}",
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="🔙 Cancel",
                        callback_data="admin:plans",
                    )
                ],
            ]
        ),
    )


@router.callback_query(F.data.startswith("admin:deleteconfirm:"))
async def admin_delete_plan(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("🚫 Admin Only!", show_alert=True)
        return

    plan_id = callback.data.split("admin:deleteconfirm:", 1)[1]
    db = load_db()

    old_count = len(db.get("plans", []))
    db["plans"] = [
        p for p in db.get("plans", [])
        if p.get("id") != plan_id
    ]

    if len(db["plans"]) == old_count:
        await callback.answer("Plan not found.", show_alert=True)
        return

    save_db(db)
    await callback.answer("🗑 Plan deleted.", show_alert=True)
    await admin_plans(callback, state)


# ============================================================
# ADMIN: ACTIVE KEYS
# ============================================================

@router.callback_query(F.data == "admin:keys")
async def admin_keys(callback: CallbackQuery, state: FSMContext):
    await state.clear()

    if not is_admin(callback.from_user.id):
        await callback.answer("🚫 Admin Only!", show_alert=True)
        return

    await callback.answer()

    db = load_db()
    current = now_ts()
    changed = False

    for item in db.get("keys", {}).values():
        if item.get("status") == "active" and item.get("expires_at", 0) <= current:
            item["status"] = "expired"
            changed = True

    if changed:
        save_db(db)

    active = [
        item for item in db.get("keys", {}).values()
        if item.get("status") == "active"
    ]

    active.sort(key=lambda x: x.get("created_at", 0), reverse=True)

    if not active:
        await callback.message.edit_text(
            f"🔑 <b>{BRAND}</b>\n\n"
            f"No active API keys.",
            reply_markup=back_admin_kb(),
        )
        return

    text = f"🔑 <b>{BRAND}</b>\n\n<b>ACTIVE API KEYS</b>\n\n"

    for item in active[:20]:
        text += (
            f"🟢 <b>{escape_html(item.get('plan_name', '-'))}</b>\n"
            f"👤 User: <code>{item.get('user_id')}</code>\n"
            f"🔑 <code>{item.get('key')}</code>\n"
            f"📊 {item.get('used', 0):,} / {item.get('limit', 0):,}\n"
            f"📅 {fmt_date(item.get('expires_at'))}\n\n"
        )

    await callback.message.edit_text(
        text,
        reply_markup=back_admin_kb(),
    )


# ============================================================
# ADMIN: STATS
# ============================================================

@router.callback_query(F.data == "admin:stats")
async def admin_stats(callback: CallbackQuery, state: FSMContext):
    await state.clear()

    if not is_admin(callback.from_user.id):
        await callback.answer("🚫 Admin Only!", show_alert=True)
        return

    await callback.answer()

    db = load_db()

    current = now_ts()
    active_keys = 0
    expired_keys = 0

    for item in db.get("keys", {}).values():
        if item.get("status") == "active" and item.get("expires_at", 0) > current:
            active_keys += 1
        elif item.get("status") == "expired" or item.get("expires_at", 0) <= current:
            expired_keys += 1

    pending = sum(
        1 for r in db.get("requests", {}).values()
        if r.get("status") == "pending"
    )

    text = (
        f"📊 <b>{BRAND}</b>\n\n"
        f"<b>API SELLING STATISTICS</b>\n\n"
        f"📦 Total Plans: <b>{len(db.get('plans', []))}</b>\n"
        f"📨 Total Requests: <b>{len(db.get('requests', {}))}</b>\n"
        f"⏳ Pending: <b>{pending}</b>\n"
        f"✅ Approved: <b>{db.get('stats', {}).get('approved', 0)}</b>\n"
        f"❌ Rejected: <b>{db.get('stats', {}).get('rejected', 0)}</b>\n"
        f"🔑 Total Keys: <b>{len(db.get('keys', {}))}</b>\n"
        f"🟢 Active Keys: <b>{active_keys}</b>\n"
        f"⌛ Expired Keys: <b>{expired_keys}</b>"
    )

    await callback.message.edit_text(
        text,
        reply_markup=back_admin_kb(),
    )


# ============================================================
# WEB API - VERIFICATION ONLY
# ============================================================

async def health(request: web.Request):
    return web.json_response(
        {
            "success": True,
            "service": BRAND,
            "status": "online",
            "type": "api_selling",
        }
    )


async def api_verify(request: web.Request):
    api_key = request.query.get("api_key", "").strip()

    if not api_key:
        return web.json_response(
            {
                "success": False,
                "error": "api_key_required",
            },
            status=400,
        )

    db = load_db()
    record = db.get("keys", {}).get(api_key)

    if not record:
        return web.json_response(
            {
                "success": False,
                "error": "invalid_api_key",
            },
            status=401,
        )

    current = now_ts()

    if record.get("status") != "active":
        return web.json_response(
            {
                "success": False,
                "error": "inactive_api_key",
                "status": record.get("status"),
            },
            status=401,
        )

    if current >= int(record.get("expires_at", 0)):
        record["status"] = "expired"
        save_db(db)

        return web.json_response(
            {
                "success": False,
                "error": "api_key_expired",
                "expires_at": record.get("expires_at"),
            },
            status=401,
        )

    used = int(record.get("used", 0))
    limit = int(record.get("limit", 0))
    remaining = max(0, limit - used)

    return web.json_response(
        {
            "success": True,
            "valid": True,
            "brand": BRAND,
            "plan": record.get("plan_name"),
            "api_key": api_key,
            "usage": {
                "used": used,
                "limit": limit,
                "remaining": remaining,
            },
            "expires_at": record.get("expires_at"),
            "expires_at_utc": fmt_date(record.get("expires_at")),
            "status": "active",
        }
    )


async def start_web_server():
    app = web.Application()

    app.router.add_get("/", health)
    app.router.add_get("/api/verify", api_verify)

    runner = web.AppRunner(app)
    await runner.setup()

    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()

    log.info("Health/API server running on port %s", PORT)


# ============================================================
# MAIN
# ============================================================

async def main():
    # Create DB if it does not exist.
    if not os.path.exists(DATA_FILE):
        save_db(json.loads(json.dumps(DEFAULT_DB)))

    await start_web_server()

    bot = Bot(
        token=BOT_TOKEN,
        default=DefaultBotProperties(
            parse_mode=ParseMode.HTML
        ),
    )

    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)

    me = await bot.get_me()

    log.info(
        "@%s — %s API Selling Bot %s started!",
        me.username,
        BRAND,
        VERSION,
    )

    await bot.delete_webhook(drop_pending_updates=False)

    try:
        await bot.send_message(
            next(iter(ADMIN_IDS)),
            f"🚀 <b>{BRAND}</b>\n\n"
            f"API Selling Bot is online.\n"
            f"Version: <b>{VERSION}</b>\n"
            f"Bot: @{escape_html(me.username or '')}\n"
            f"🌐 Port: <b>{PORT}</b>",
        )
    except Exception as e:
        log.warning("Startup admin notification failed: %s", e)

    try:
        await dp.start_polling(
            bot,
            allowed_updates=dp.resolve_used_update_types(),
        )
    finally:
        await bot.session.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("Bot stopped.")
