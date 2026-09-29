import os
import json
import secrets
import hashlib
import asyncio
from datetime import datetime, timezone, timedelta

from aiohttp import web
from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_IDS = {
    int(x.strip()) for x in os.getenv("ADMIN_IDS", "").split(",")
    if x.strip().isdigit()
}
PORT = int(os.getenv("PORT", "10000"))
DATA_FILE = os.getenv("DATA_FILE", "krutik_api_data.json")

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN environment variable is required.")
if not ADMIN_IDS:
    raise RuntimeError("ADMIN_IDS environment variable is required.")

DEFAULT_PLAN = {
    "name": "Basic",
    "days": 30,
    "daily_limit": 1000,
    "max_count": 1,
}

def now():
    return datetime.now(timezone.utc)

def iso(dt):
    return dt.isoformat()

def load():
    if not os.path.exists(DATA_FILE):
        return {
            "api_enabled": True,
            "firebases": [],
            "plans": {"basic": DEFAULT_PLAN},
            "keys": {},
            "requests": {},
            "stats": {"api_requests": 0, "successful_requests": 0},
        }
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            d = json.load(f)
    except Exception:
        d = {}
    d.setdefault("api_enabled", True)
    d.setdefault("firebases", [])
    d.setdefault("plans", {"basic": DEFAULT_PLAN})
    d.setdefault("keys", {})
    d.setdefault("requests", {})
    d.setdefault("stats", {"api_requests": 0, "successful_requests": 0})
    return d

DATA = load()
LOCK = asyncio.Lock()

def save():
    tmp = DATA_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(DATA, f, indent=2, ensure_ascii=False)
    os.replace(tmp, DATA_FILE)

def key_hash(key):
    return hashlib.sha256(key.encode()).hexdigest()

def make_key():
    return "KCE-" + secrets.token_hex(20).upper()

def get_key(raw):
    return DATA["keys"].get(key_hash(raw.strip()))

def key_valid(rec):
    if not rec.get("active"):
        return False, "API_KEY_DISABLED"
    try:
        if now() >= datetime.fromisoformat(rec["expires_at"]):
            return False, "API_KEY_EXPIRED"
    except Exception:
        return False, "INVALID_EXPIRY"
    return True, "OK"

def admin(uid):
    return uid in ADMIN_IDS

def kb(rows):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t, callback_data=c) for t, c in row]
        for row in rows
    ])

class AddFirebase(StatesGroup):
    waiting = State()

class CreateKey(StatesGroup):
    waiting_user = State()

bot = Bot(BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

@dp.message(CommandStart())
async def start(message: Message):
    if admin(message.from_user.id):
        await message.answer(
            "👑 KRUTIK CYBER EXPERT\n\n"
            "Admin Panel",
            reply_markup=kb([
                [("🔥 Firebase Manager", "fb_menu")],
                [("🔑 API Keys", "keys"), ("📊 Statistics", "stats")],
                [("🔌 API ON/OFF", "toggle")]
            ])
        )
    else:
        await message.answer(
            "KRUTIK CYBER EXPERT API\n\n"
            "API users do not enter Firebase URLs.\n"
            "Firebase configuration is managed server-side by the administrator."
        )

@dp.callback_query(F.data == "toggle")
async def toggle(call: CallbackQuery):
    if not admin(call.from_user.id):
        return await call.answer("Admin only", show_alert=True)
    DATA["api_enabled"] = not DATA["api_enabled"]
    save()
    state = "🟢 ON" if DATA["api_enabled"] else "🔴 OFF"
    await call.message.edit_text(
        f"🔌 API STATUS\n\nCurrent: <b>{state}</b>",
        parse_mode="HTML",
        reply_markup=kb([
            [("🔄 Toggle", "toggle")],
            [("⬅️ Back", "back")]
        ])
    )
    await call.answer()

@dp.callback_query(F.data == "back")
async def back(call: CallbackQuery):
    if not admin(call.from_user.id):
        return await call.answer("Admin only", show_alert=True)
    await call.message.edit_text(
        "👑 Admin Panel",
        reply_markup=kb([
            [("🔥 Firebase Manager", "fb_menu")],
            [("🔑 API Keys", "keys"), ("📊 Statistics", "stats")],
            [("🔌 API ON/OFF", "toggle")]
        ])
    )

@dp.callback_query(F.data == "fb_menu")
async def fb_menu(call: CallbackQuery):
    if not admin(call.from_user.id):
        return await call.answer("Admin only", show_alert=True)

    fbs = DATA["firebases"]
    text = f"🔥 Firebase Manager\n\nTotal: {len(fbs)}\n\n"
    if fbs:
        for i, fb in enumerate(fbs, 1):
            text += f"{i}. {fb['label']}\n   {fb['url']}\n\n"
    else:
        text += "No Firebase URLs configured.\n"

    await call.message.edit_text(
        text,
        reply_markup=kb([
            [("➕ Add Firebase", "fb_add")],
            [("🗑️ Remove Firebase", "fb_remove")],
            [("⬅️ Back", "back")]
        ])
    )

@dp.callback_query(F.data == "fb_add")
async def fb_add(call: CallbackQuery, state: FSMContext):
    if not admin(call.from_user.id):
        return await call.answer("Admin only", show_alert=True)
    await state.set_state(AddFirebase.waiting)
    await call.message.answer(
        "🔥 Send Firebase in this format:\n\n"
        "<code>Label | https://your-project.firebaseio.com</code>\n\n"
        "This URL is stored only on the server/admin side.",
        parse_mode="HTML"
    )
    await call.answer()

@dp.message(AddFirebase.waiting)
async def fb_add_done(message: Message, state: FSMContext):
    if not admin(message.from_user.id):
        return
    text = (message.text or "").strip()
    if "|" in text:
        label, url = [x.strip() for x in text.split("|", 1)]
    else:
        label, url = "Firebase", text

    if not url.startswith(("http://", "https://")):
        return await message.answer("❌ URL must start with http:// or https://")

    url = url.rstrip("/")
    if any(x["url"] == url for x in DATA["firebases"]):
        await state.clear()
        return await message.answer("⚠️ Firebase URL already exists.")

    DATA["firebases"].append({
        "id": secrets.token_hex(6),
        "label": label[:50],
        "url": url,
        "added_at": iso(now())
    })
    save()
    await state.clear()
    await message.answer("✅ Firebase added to the server-side configuration.")

@dp.callback_query(F.data == "fb_remove")
async def fb_remove(call: CallbackQuery):
    if not admin(call.from_user.id):
        return await call.answer("Admin only", show_alert=True)
    if not DATA["firebases"]:
        return await call.answer("No Firebase configured.", show_alert=True)

    rows = []
    for fb in DATA["firebases"]:
        rows.append([(f"🗑️ {fb['label']}", f"fb_del:{fb['id']}")])
    rows.append([("⬅️ Back", "fb_menu")])
    await call.message.edit_text("Select Firebase to remove:", reply_markup=kb(rows))
    await call.answer()

@dp.callback_query(F.data.startswith("fb_del:"))
async def fb_del(call: CallbackQuery):
    if not admin(call.from_user.id):
        return await call.answer("Admin only", show_alert=True)
    fid = call.data.split(":", 1)[1]
    before = len(DATA["firebases"])
    DATA["firebases"] = [x for x in DATA["firebases"] if x["id"] != fid]
    save()
    await call.answer("Deleted" if len(DATA["firebases"]) < before else "Not found")
    await fb_menu(call)

@dp.callback_query(F.data == "keys")
async def keys(call: CallbackQuery):
    if not admin(call.from_user.id):
        return await call.answer("Admin only", show_alert=True)
    active = sum(1 for x in DATA["keys"].values() if x.get("active"))
    await call.message.edit_text(
        f"🔑 API Keys\n\nActive keys: {active}",
        reply_markup=kb([
            [("➕ Create Key", "create_key")],
            [("🔒 Disable Key", "disable_key")],
            [("⬅️ Back", "back")]
        ])
    )
    await call.answer()

@dp.callback_query(F.data == "create_key")
async def create_key(call: CallbackQuery, state: FSMContext):
    if not admin(call.from_user.id):
        return await call.answer("Admin only", show_alert=True)
    await state.set_state(CreateKey.waiting_user)
    await call.message.answer("Send the customer Telegram user ID:")
    await call.answer()

@dp.message(CreateKey.waiting_user)
async def create_key_done(message: Message, state: FSMContext):
    if not admin(message.from_user.id):
        return
    user_id = (message.text or "").strip()
    if not user_id.isdigit():
        return await message.answer("❌ User ID must be numeric.")

    plan = DATA["plans"]["basic"]
    raw = make_key()
    expires = now() + timedelta(days=int(plan["days"]))
    DATA["keys"][key_hash(raw)] = {
        "user_id": user_id,
        "plan": "basic",
        "active": True,
        "created_at": iso(now()),
        "expires_at": iso(expires),
        "daily_limit": int(plan["daily_limit"]),
        "used_today": 0,
        "max_count": 1
    }
    save()
    await state.clear()
    await message.answer(
        "✅ API key created.\n\n"
        f"<code>{raw}</code>\n\n"
        f"Expires: {iso(expires)}\n"
        f"Daily limit: {plan['daily_limit']}\n"
        "Max count per request: 1",
        parse_mode="HTML"
    )

@dp.callback_query(F.data == "disable_key")
async def disable_key(call: CallbackQuery):
    if not admin(call.from_user.id):
        return await call.answer("Admin only", show_alert=True)
    await call.message.answer(
        "For safety, key disabling is available through the server API only:\n"
        "POST /admin/key/disable with X-Admin-ID."
    )
    await call.answer()

@dp.callback_query(F.data == "stats")
async def stats(call: CallbackQuery):
    if not admin(call.from_user.id):
        return await call.answer("Admin only", show_alert=True)
    s = DATA["stats"]
    await call.message.edit_text(
        f"📊 Statistics\n\n"
        f"API requests: {s['api_requests']}\n"
        f"Successful requests: {s['successful_requests']}\n"
        f"Firebase configs: {len(DATA['firebases'])}\n"
        f"API: {'🟢 ON' if DATA['api_enabled'] else '🔴 OFF'}",
        reply_markup=kb([[("⬅️ Back", "back")]])
    )
    await call.answer()

async def health(request):
    return web.json_response({
        "success": True,
        "service": "KRUTIK CYBER EXPERT API",
        "api_enabled": DATA["api_enabled"],
        "firebase_configured": len(DATA["firebases"]) > 0
    })

async def verify(request):
    raw = request.query.get("api_key", "").strip()
    if not raw:
        return web.json_response({"success": False, "error": "API_KEY_REQUIRED"}, status=400)
    rec = get_key(raw)
    if not rec:
        return web.json_response({"success": False, "error": "INVALID_API_KEY"}, status=401)
    ok, reason = key_valid(rec)
    if not ok:
        return web.json_response({"success": False, "error": reason}, status=401)
    return web.json_response({
        "success": True,
        "status": "active",
        "plan": rec["plan"],
        "expires_at": rec["expires_at"],
        "usage": {
            "used": rec["used_today"],
            "limit": rec["daily_limit"],
            "remaining": max(0, rec["daily_limit"] - rec["used_today"])
        }
    })

async def api_send(request):
    # This endpoint intentionally does not expose or accept Firebase URLs.
    # It is an authorization/validation layer for an authorized transactional
    # messaging provider. It does NOT call the original SMS-bombing function.
    if not DATA["api_enabled"]:
        return web.json_response({
            "success": False,
            "status": "offline",
            "error": "API temporarily disabled by administrator"
        }, status=503)

    try:
        body = await request.json()
    except Exception:
        return web.json_response({"success": False, "error": "INVALID_JSON"}, status=400)

    raw = str(body.get("api_key", "")).strip()
    number = str(body.get("number", "")).strip()
    message = str(body.get("message", "")).strip()
    count = body.get("count", 1)

    if not raw:
        return web.json_response({"success": False, "error": "API_KEY_REQUIRED"}, status=400)

    rec = get_key(raw)
    if not rec:
        return web.json_response({"success": False, "error": "INVALID_API_KEY"}, status=401)

    ok, reason = key_valid(rec)
    if not ok:
        return web.json_response({"success": False, "error": reason}, status=401)

    if not number or len(number) > 32:
        return web.json_response({"success": False, "error": "INVALID_NUMBER"}, status=400)

    if not message or len(message) > 1000:
        return web.json_response({"success": False, "error": "INVALID_MESSAGE"}, status=400)

    try:
        count = int(count)
    except Exception:
        return web.json_response({"success": False, "error": "INVALID_COUNT"}, status=400)

    if count != 1:
        return web.json_response({
            "success": False,
            "error": "ONLY_SINGLE_TRANSACTIONAL_MESSAGE_ALLOWED",
            "allowed_count": 1
        }, status=429)

    if not DATA["firebases"]:
        return web.json_response({
            "success": False,
            "error": "NO_SERVER_BACKEND_CONFIGURED"
        }, status=503)

    # Deliberately stop here. The old backend is a repeated SMS-broadcast/
    # bombing system, so this API does not invoke that function.
    return web.json_response({
        "success": False,
        "error": "AUTHORIZED_MESSAGING_PROVIDER_REQUIRED",
        "message": (
            "Firebase configuration is present, but this API does not "
            "invoke the legacy bulk/repeated SMS backend."
        )
    }, status=503)

async def admin_status(request):
    uid = request.headers.get("X-Admin-ID", "")
    if not uid.isdigit() or int(uid) not in ADMIN_IDS:
        return web.json_response({"success": False, "error": "ADMIN_ONLY"}, status=403)
    return web.json_response({
        "success": True,
        "api_enabled": DATA["api_enabled"],
        "firebase_count": len(DATA["firebases"]),
        "key_count": len(DATA["keys"])
    })

async def admin_toggle(request):
    uid = request.headers.get("X-Admin-ID", "")
    if not uid.isdigit() or int(uid) not in ADMIN_IDS:
        return web.json_response({"success": False, "error": "ADMIN_ONLY"}, status=403)
    try:
        body = await request.json()
        enabled = body["enabled"]
        if not isinstance(enabled, bool):
            raise ValueError()
    except Exception:
        return web.json_response({"success": False, "error": "enabled must be boolean"}, status=400)
    DATA["api_enabled"] = enabled
    save()
    return web.json_response({"success": True, "api_enabled": enabled})

async def admin_disable_key(request):
    uid = request.headers.get("X-Admin-ID", "")
    if not uid.isdigit() or int(uid) not in ADMIN_IDS:
        return web.json_response({"success": False, "error": "ADMIN_ONLY"}, status=403)
    try:
        body = await request.json()
        raw = str(body["api_key"]).strip()
    except Exception:
        return web.json_response({"success": False, "error": "api_key required"}, status=400)
    rec = get_key(raw)
    if not rec:
        return web.json_response({"success": False, "error": "INVALID_API_KEY"}, status=404)
    rec["active"] = False
    save()
    return web.json_response({"success": True, "status": "disabled"})

async def web_server():
    app = web.Application()
    app.router.add_get("/", health)
    app.router.add_get("/api/verify", verify)
    app.router.add_post("/api/send", api_send)
    app.router.add_get("/admin/status", admin_status)
    app.router.add_post("/admin/api/toggle", admin_toggle)
    app.router.add_post("/admin/key/disable", admin_disable_key)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", PORT).start()
    print(f"API server running on port {PORT}")

async def main():
    await web_server()
    print("KRUTIK CYBER EXPERT bot starting...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
