from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Awaitable, Callable

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram import Bot
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from .membership import (
    PRODUCTS,
    MembershipConfig,
    MembershipStore,
)
from .profiles import UserProfile, UserProfileStore

LOGGER = logging.getLogger("vortex_alpha_network")

WELCOME_MESSAGE = (
    "Welcome to Vortex Alpha Network.\n\n"
    "Choose a channel or account option below. Free Updates is public. "
    "Crypto Alpha, Sport Edge, and Remote Jobs are members-only areas."
)

ONBOARDING_MESSAGE = (
    "🌍 Welcome to Vortex Alpha Network\n\n"
    "Your global hub for verified jobs, crypto alpha, sports, opportunities, "
    "education, business information and important updates.\n\n"
    "First, choose your language."
)

COUNTRY_MESSAGE = "📍 Where are you located?"

INTERESTS_MESSAGE = (
    "🎯 What are you interested in?\n\n"
    "Select all that apply, then tap Continue."
)

PUBLIC_CHANNEL_MESSAGE = (
    "📢 Stay Connected\n\n"
    "Join the official Vortex Alpha Network public channel to receive free "
    "verified updates and important alerts.\n\n"
    "You can continue even if membership verification is not available."
)

HOME_MESSAGE = (
    "🌍 VORTEX ALPHA NETWORK\n\n"
    "Your home for verified opportunities, market information, sports updates, "
    "and important alerts."
)

LANGUAGES = {
    "en": "🇬🇧 English",
    "fr": "🇫🇷 Français",
    "es": "🇪🇸 Español",
    "pt": "🇵🇹 Português",
    "de": "🇩🇪 Deutsch",
    "ar": "🇸🇦 العربية",
    "zh": "🇨🇳 中文",
    "ja": "🇯🇵 日本語",
    "hi": "🇮🇳 हिन्दी",
    "other": "🌐 Other",
}

COUNTRIES = {
    "worldwide": "🌍 Worldwide / No preference",
    "nigeria": "🇳🇬 Nigeria",
    "uk": "🇬🇧 United Kingdom",
    "us": "🇺🇸 United States",
    "canada": "🇨🇦 Canada",
    "australia": "🇦🇺 Australia",
    "south_africa": "🇿🇦 South Africa",
    "kenya": "🇰🇪 Kenya",
    "ghana": "🇬🇭 Ghana",
    "india": "🇮🇳 India",
    "uae": "🇦🇪 UAE",
    "europe": "🇪🇺 Europe",
    "other": "🌎 Other country",
}

INTERESTS = {
    "remote_jobs": "💼 Remote Jobs",
    "crypto_alpha": "⚡ Crypto Alpha",
    "sports_betting": "⚽ Sports & Betting",
    "education": "🎓 Education",
    "opportunities": "💰 Opportunities & Grants",
    "business_finance": "🏢 Business & Finance",
    "global_updates": "🌍 Global Updates",
    "news": "📰 News",
    "important_alerts": "🔔 Important Alerts",
}

COMING_SOON = {
    "education": "Education",
    "opportunities": "Opportunities",
    "business": "Business",
    "global": "Global Updates",
    "news": "News",
}

AREA_PRODUCTS = {
    "crypto": "crypto",
    "remote": "jobs",
    "jobs": "jobs",
    "sport": "sport",
}

HELP_ARTICLES = {
    "getting_started": (
        "🚀 Getting Started\n\n"
        "Use /start to begin onboarding. Choose your language, country or "
        "region, and interests so Vortex can organize the experience around "
        "what matters to you. You can change these choices from Settings at "
        "any time."
    ),
    "remote": (
        "💼 Remote Jobs\n\n"
        "This section is for legitimate remote work and work-from-anywhere "
        "opportunities. Always review the original source and application "
        "link. Vortex does not guarantee that any user will be hired."
    ),
    "crypto": (
        "⚡ Crypto Alpha\n\n"
        "This members-only area is for crypto research and market information. "
        "Crypto involves financial risk. Vortex never promises guaranteed "
        "profits and does not invent prices, projects, announcements, or news."
    ),
    "sport": (
        "⚽ Sport Edge\n\n"
        "This members-only area may include fixtures, results, statistics, "
        "sports information, and betting-related analysis where available. "
        "Vortex never claims guaranteed betting wins."
    ),
    "premium": (
        "⭐ Premium\n\n"
        "Premium areas are separate products: Crypto Alpha, Sport Edge, and "
        "Remote Jobs. A verified subscription unlocks only the matching area; "
        "it does not grant access to the other private channels."
    ),
    "payments": (
        "💳 Payments\n\n"
        "Payments and subscription confirmations are handled through the "
        "configured Tribute flow. Never send payment details in chat. Access "
        "is granted only after a legitimate confirmation is received."
    ),
    "alerts": (
        "🔔 Alerts\n\n"
        "Your alert preference can be switched on or off from Settings. Your "
        "interests help organize which updates are relevant to you."
    ),
    "profile": (
        "👤 Profile\n\n"
        "My Profile shows your saved language, country or region, interests, "
        "and separate premium status for each membership area."
    ),
    "settings": (
        "⚙️ Settings\n\n"
        "Use Settings to change your language, country or region, interests, "
        "and alert preference. Changes are saved for your Telegram account."
    ),
    "coming_soon": (
        "🎓 Coming Soon\n\n"
        "Education, Opportunities, Business, Global Updates, and News are "
        "being prepared and are not active channels yet."
    ),
}


def language_keyboard() -> InlineKeyboardMarkup:
    languages = list(LANGUAGES.items())
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(label, callback_data=f"language:{code}")
                for code, label in languages[index : index + 2]
            ]
            for index in range(0, len(languages), 2)
        ]
    )


def country_keyboard(flow: str = "onboarding") -> InlineKeyboardMarkup:
    countries = list(COUNTRIES.items())
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    label, callback_data=f"country:{flow}:{code}"
                )
                for code, label in countries[index : index + 2]
            ]
            for index in range(0, len(countries), 2)
        ]
    )


def interest_keyboard(
    selected: tuple[str, ...] = (),
    *,
    flow: str = "onboarding",
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for index in range(0, len(INTERESTS), 2):
        row = []
        for key, label in list(INTERESTS.items())[index : index + 2]:
            prefix = "✅ " if key in selected else ""
            callback_prefix = "interest" if flow == "onboarding" else "settings:interest"
            row.append(
                InlineKeyboardButton(
                    f"{prefix}{label}",
                    callback_data=f"{callback_prefix}:{key}",
                )
            )
        rows.append(row)
    rows.append(
        [
            InlineKeyboardButton(
                "✅ Continue" if flow == "onboarding" else "✅ Done",
                callback_data=(
                    "onboarding:interests:continue"
                    if flow == "onboarding"
                    else "settings:interests:done"
                ),
            )
        ]
    )
    return InlineKeyboardMarkup(rows)


def menu_keyboard() -> InlineKeyboardMarkup:
    buttons = [
        ("Free Updates", "area:free"),
        ("Crypto Alpha", "area:crypto"),
        ("Sport Edge", "area:sport"),
        ("Remote Jobs", "area:remote"),
        ("VIP Membership", "area:vip"),
        ("My Account", "area:account"),
        ("Help", "area:help"),
    ]
    rows = [
        [
            InlineKeyboardButton(label, callback_data=data)
            for label, data in buttons[index : index + 2]
        ]
        for index in range(0, len(buttons), 2)
    ]
    return InlineKeyboardMarkup(rows)


def back_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("Back to menu", callback_data="menu:home")]]
    )


async def send_activation_confirmation(
    bot: Bot,
    *,
    config: MembershipConfig,
    telegram_user_id: int,
    product: str,
) -> None:
    """Send only the newly unlocked product's private-channel action."""
    private_link = config.private_channel_links[product]
    if not private_link:
        LOGGER.error(
            "Verified %s membership for Telegram user %s has no private channel link.",
            product,
            telegram_user_id,
        )
        return

    values = PRODUCTS[product]
    try:
        await bot.send_message(
            chat_id=telegram_user_id,
            text=(
                f"Your {values['name']} membership is verified. "
                "Use the button below to join your private channel."
            ),
            reply_markup=InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            f"Join {values['name']}",
                            url=private_link,
                        )
                    ]
                ]
            ),
        )
    except Exception:
        LOGGER.exception(
            "Unable to send %s activation notification to Telegram user %s.",
            product,
            telegram_user_id,
        )


def _activation_notifier(
    loop: asyncio.AbstractEventLoop,
    bot: Bot,
    config: MembershipConfig,
) -> Callable[[int, str], None]:
    def notify(telegram_user_id: int, product: str) -> None:
        future = asyncio.run_coroutine_threadsafe(
            send_activation_confirmation(
                bot,
                config=config,
                telegram_user_id=telegram_user_id,
                product=product,
            ),
            loop,
        )
        future.add_done_callback(_log_notification_task_failure)

    return notify


def _log_notification_task_failure(future: "asyncio.Future[object]") -> None:
    try:
        future.result()
    except Exception:
        LOGGER.exception("Unexpected activation notification task failure.")


def _services(context: ContextTypes.DEFAULT_TYPE) -> tuple[
    MembershipConfig, MembershipStore
]:
    return (
        context.application.bot_data["membership_config"],
        context.application.bot_data["membership_store"],
    )


def area_keyboard(
    area: str,
    *,
    config: MembershipConfig,
    store: MembershipStore,
    telegram_user_id: int | None,
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    if area in PRODUCTS:
        is_active = bool(
            telegram_user_id and store.is_active(telegram_user_id, area)
        )
        private_link = config.private_channel_links[area]
        if is_active and private_link:
            rows.append(
                [
                    InlineKeyboardButton(
                        f"Join {PRODUCTS[area]['name']}",
                        url=private_link,
                    )
                ]
            )
        tribute_link = config.tribute_links[area]
        if not is_active and tribute_link:
            rows.append(
                [
                    InlineKeyboardButton(
                        f"Subscribe — {PRODUCTS[area]['price']}",
                        url=tribute_link,
                    )
                ]
            )
    elif area == "vip":
        for product, values in PRODUCTS.items():
            tribute_link = config.tribute_links[product]
            if tribute_link:
                rows.append(
                    [
                        InlineKeyboardButton(
                            f"Subscribe to {values['name']} — {values['price']}",
                            url=tribute_link,
                        )
                    ]
                )
    elif area == "free" and config.public_channel_link:
        rows.append(
            [
                InlineKeyboardButton(
                    "Join public channel",
                    url=config.public_channel_link,
                )
            ]
        )
    rows.append([InlineKeyboardButton("Back to menu", callback_data="menu:home")])
    return InlineKeyboardMarkup(rows)


async def welcome(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    del context
    if update.message is None:
        return
    await update.message.reply_text(
        ONBOARDING_MESSAGE,
        reply_markup=language_keyboard(),
    )


async def show_language_selection(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    del context
    query = update.callback_query
    if query is None:
        return
    await query.answer("Language selection will continue in the next onboarding step.")


async def show_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    del context
    query = update.callback_query
    if query is None or query.message is None:
        return
    await query.answer()
    await query.edit_message_text(
        WELCOME_MESSAGE,
        reply_markup=menu_keyboard(),
    )


async def show_area(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None or query.message is None or not query.data:
        return

    await query.answer()
    area = query.data.removeprefix("area:")
    config, store = _services(context)
    user = update.effective_user
    await query.edit_message_text(
        area_message(area, update, config=config, store=store),
        reply_markup=area_keyboard(
            area,
            config=config,
            store=store,
            telegram_user_id=user.id if user else None,
        ),
    )


def area_message(
    area: str,
    update: Update,
    *,
    config: MembershipConfig | None = None,
    store: MembershipStore | None = None,
) -> str:
    if area == "free":
        return (
            "Free Updates\n\n"
            "This is the public Vortex Alpha Network channel for free updates, "
            "verified opportunities, and important announcements.\n\n"
            + (
                "Use the button below to join the public channel."
                if config and config.public_channel_link
                else "The public channel link will be added when channel setup is complete."
            )
        )
    if area in PRODUCTS:
        values = PRODUCTS[area]
        user = update.effective_user
        is_active = bool(
            user and store and store.is_active(user.id, area)
        )
        if is_active:
            if config and config.private_channel_links[area]:
                access_text = (
                    "Your membership is verified. Use the button below to join "
                    "this private channel."
                )
            else:
                LOGGER.error("Verified %s membership has no private channel link.", area)
                access_text = (
                    "Your membership is verified, but this channel link is temporarily "
                    "unavailable. Please contact support."
                )
        else:
            status = store.status_for(user.id, area) if user and store else None
            if status in {"pending", "awaiting_confirmation"}:
                access_text = (
                    "Your payment is awaiting Tribute confirmation. This channel will "
                    "remain locked until a verified confirmation is received."
                )
            else:
                access_text = (
                    "This area is members-only. Subscribe using the button below. "
                    "Access is granted only after Tribute confirms payment."
                )
        return (
            f"Vortex {values['name']}\n\n"
            f"Members-only area for {values['name'].lower()} updates.\n\n"
            f"Price: {values['price']}\n"
            f"{access_text}"
        )
    if area == "vip":
        return (
            "VIP Membership\n\n"
            "Subscribe to each premium area separately. A successful payment unlocks "
            "only the matching private channel.\n\n"
            "⚡ Crypto Alpha — $10/month\n"
            "💼 Remote Jobs — $5/month\n"
            "⚽ Sport Edge — $5/month\n\n"
            "After Tribute confirms a subscription, return to that area to receive "
            "its private channel link."
        )
    if area == "account":
        user = update.effective_user
        name = user.full_name if user else "there"
        username = f"@{user.username}" if user and user.username else "No username set"
        membership_lines = []
        for product, values in PRODUCTS.items():
            status = (
                store.status_for(user.id, product)
                if user and store
                else None
            )
            label = {
                "active": "Active",
                "paid": "Active",
                "succeeded": "Active",
                "completed": "Active",
                "trialing": "Active",
                "pending": "Awaiting confirmation",
                "awaiting_confirmation": "Awaiting confirmation",
                "expired": "Expired",
            }.get(status, "Not active")
            membership_lines.append(f"{values['name']}: {label}")
        return (
            "My Account\n\n"
            f"Name: {name}\n"
            f"Telegram: {username}\n\n"
            + "\n".join(membership_lines)
            + "\n\n"
            "Private channel access is granted separately for each verified "
            "subscription."
        )
    if area == "help":
        return (
            "Help\n\n"
            "Use /start or /command1 at any time to reopen the menu.\n"
            "Free Updates is public. Crypto Alpha, Sport Edge, and Remote Jobs "
            "are members-only areas. Choose VIP Membership to subscribe through "
            "Tribute. Payment confirmation must be verified before access is granted."
        )
    return WELCOME_MESSAGE


def build_application(
    token: str,
    *,
    config: MembershipConfig | None = None,
    store: MembershipStore | None = None,
    post_init: Callable[[Application], Awaitable[None]] | None = None,
) -> Application:
    config = config or MembershipConfig.from_env()
    store = store or MembershipStore(config.database_path)
    builder = Application.builder().token(token)
    if post_init:
        builder = builder.post_init(post_init)
    application = builder.build()
    application.bot_data["membership_config"] = config
    application.bot_data["membership_store"] = store
    application.add_handler(CommandHandler(["start", "command1"], welcome))
    application.add_handler(
        CallbackQueryHandler(show_language_selection, pattern=r"^language:.+$")
    )
    application.add_handler(
        CallbackQueryHandler(show_menu, pattern=r"^menu:home$")
    )
    application.add_handler(
        CallbackQueryHandler(show_area, pattern=r"^area:.+$")
    )
    return application


def run() -> None:
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise RuntimeError(
            "TELEGRAM_BOT_TOKEN is required. Add it as a secure workspace secret."
        )

    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    )
    # Telegram API URLs contain the bot token. Keep transport logs quiet so
    # credentials never appear in workflow output.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("telegram").setLevel(logging.WARNING)
    logging.getLogger("telegram.ext").setLevel(logging.WARNING)
    config = MembershipConfig.from_env()
    store = MembershipStore(config.database_path)
    from .membership import start_tribute_webhook

    webhook_server = None

    async def post_init(application: Application) -> None:
        nonlocal webhook_server
        webhook_server = start_tribute_webhook(
            config=config,
            store=store,
            on_activation=_activation_notifier(
                asyncio.get_running_loop(),
                application.bot,
                config,
            ),
        )

    application = build_application(
        token,
        config=config,
        store=store,
        post_init=post_init,
    )
    LOGGER.info("Vortex Alpha Network bot is starting")
    try:
        application.run_polling(allowed_updates=Update.ALL_TYPES)
    finally:
        if webhook_server:
            webhook_server.shutdown()
        store.close()