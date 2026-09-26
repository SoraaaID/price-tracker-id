"""Interactive Telegram bot setup script.

Walks through:
1. Enter bot token (from @BotFather).
2. Validate via getMe API.
3. Get chat_id via getUpdates (user must send /start to the bot first).
4. Send a test message.
5. Print config.toml snippet.
"""

from __future__ import annotations

import sys
import requests


def main() -> None:
    print("=" * 50)
    print("  Telegram Bot Setup for Price Tracker")
    print("=" * 50)
    print()

    # Step 1: Get bot token
    print("Step 1: Enter your Telegram Bot Token")
    print("  (Create a bot via @BotFather on Telegram)")
    bot_token = input("  Bot Token: ").strip()

    if not bot_token:
        print("  [ERROR] Bot token cannot be empty.")
        sys.exit(1)

    # Step 2: Validate via getMe
    print("\nStep 2: Validating bot token...")
    try:
        resp = requests.get(
            f"https://api.telegram.org/bot{bot_token}/getMe",
            timeout=10,
        )
        data = resp.json()
    except Exception as e:
        print(f"  [ERROR] Failed to connect to Telegram API: {e}")
        sys.exit(1)

    if not data.get("ok"):
        print(f"  [ERROR] Invalid bot token: {data.get('description', 'Unknown error')}")
        sys.exit(1)

    bot_info = data["result"]
    print(f"  [OK] Bot: @{bot_info['username']} ({bot_info['first_name']})")

    # Step 3: Get chat_id
    print("\nStep 3: Getting your chat ID")
    print(f"  Please send /start to @{bot_info['username']} on Telegram,")
    print("  then press Enter here...")
    input("  Press Enter when ready: ")

    try:
        resp = requests.get(
            f"https://api.telegram.org/bot{bot_token}/getUpdates",
            timeout=10,
        )
        updates = resp.json()
    except Exception as e:
        print(f"  [ERROR] Failed to get updates: {e}")
        sys.exit(1)

    if not updates.get("ok") or not updates.get("result"):
        print("  [ERROR] No messages found. Make sure you sent /start to the bot.")
        sys.exit(1)

    # Get the most recent message's chat_id
    chat_id = None
    for update in reversed(updates["result"]):
        msg = update.get("message", {})
        if msg.get("chat", {}).get("id"):
            chat_id = str(msg["chat"]["id"])
            sender = msg.get("from", {}).get("username", "unknown")
            print(f"  [OK] Chat ID: {chat_id} (from @{sender})")
            break

    if not chat_id:
        print("  [ERROR] Could not find chat_id. Try sending another message to the bot.")
        sys.exit(1)

    # Step 4: Send test message
    print("\nStep 4: Sending test message...")
    try:
        resp = requests.post(
            f"https://api.telegram.org/bot{bot_token}/sendMessage",
            json={
                "chat_id": chat_id,
                "text": "Price Tracker setup complete! You will receive price alerts here.",
                "parse_mode": "Markdown",
            },
            timeout=10,
        )
        data = resp.json()
        if data.get("ok"):
            print("  [OK] Test message sent! Check your Telegram.")
        else:
            print(f"  [WARNING] Failed to send test message: {data.get('description')}")
    except Exception as e:
        print(f"  [WARNING] Failed to send test message: {e}")

    # Step 5: Print config
    print("\n" + "=" * 50)
    print("  Setup Complete!")
    print("=" * 50)
    print("\nAdd these to your config.toml:\n")
    print(f'[telegram]')
    print(f'bot_token = "{bot_token}"')
    print(f'chat_id = "{chat_id}"')
    print()
    print("Or set environment variables:")
    print(f"  TELEGRAM_BOT_TOKEN={bot_token}")
    print(f"  TELEGRAM_CHAT_ID={chat_id}")


if __name__ == "__main__":
    main()
