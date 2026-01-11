import os
import asyncio
import logging
from telethon import TelegramClient, events
from telethon.sessions import StringSession
from telethon.tl.types import MessageMediaDocument, MessageMediaPhoto
from datetime import datetime
import re

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

API_ID = int(os.getenv('API_ID', '0'))
API_HASH = os.getenv('API_HASH', '')
PHONE = os.getenv('PHONE', '')
BOT_TOKEN = os.getenv('BOT_TOKEN', '')
SESSION_STRING = os.getenv('SESSION_STRING', '')
DOWNLOAD_PATH = os.getenv('DOWNLOAD_PATH', './downloads')

os.makedirs(DOWNLOAD_PATH, exist_ok=True)

# Initialize clients with session string support
if SESSION_STRING:
    logger.info("Using session string for authentication")
    user_client = TelegramClient(StringSession(SESSION_STRING), API_ID, API_HASH)
else:
    logger.info("Using session file for authentication")
    user_client = TelegramClient('user_session', API_ID, API_HASH)

bot_client = TelegramClient('bot_session', API_ID, API_HASH)

is_downloading = False

def parse_message_link(link):
    """Parse Telegram message link with private channel support"""
    # Format: https://t.me/c/123456/789 (private) or https://t.me/channel/123 (public)
    pattern = r't\.me/(?:c/)?([^/]+)/(\d+)'
    match = re.search(pattern, link)
    if match:
        channel = match.group(1)
        msg_id = int(match.group(2))
        
        # If it's a private channel (numeric), convert to proper format
        if channel.isdigit():
            # Private channels need -100 prefix
            channel = int('-100' + channel)
        
        return channel, msg_id
    return None, None

async def download_media(message, custom_path=None):
    try:
        download_dir = custom_path or DOWNLOAD_PATH
        
        if message.media:
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            
            if isinstance(message.media, MessageMediaDocument):
                # Try to get filename from attributes
                file_name = None
                for attr in message.media.document.attributes:
                    if hasattr(attr, 'file_name'):
                        file_name = attr.file_name
                        break
                if not file_name:
                    file_name = f'file_{timestamp}'
                file_size = message.media.document.size
            elif isinstance(message.media, MessageMediaPhoto):
                file_name = f'photo_{timestamp}.jpg'
                file_size = 0
            else:
                file_name = f'media_{timestamp}'
                file_size = 0
            
            file_path = os.path.join(download_dir, file_name)
            
            logger.info(f"Starting download: {file_name} ({file_size / (1024**3):.2f} GB)")
            
            def progress_callback(current, total):
                percent = (current / total) * 100
                if int(percent) % 10 == 0:
                    logger.info(f"Progress: {percent:.1f}%")
            
            await user_client.download_media(
                message,
                file=file_path,
                progress_callback=progress_callback
            )
            
            logger.info(f"✅ Downloaded: {file_name}")
            return file_path
        else:
            return None
            
    except Exception as e:
        logger.error(f"Download failed: {str(e)}")
        return None

async def download_single(link, notify_chat_id):
    try:
        channel, msg_id = parse_message_link(link)
        if not channel or not msg_id:
            await bot_client.send_message(notify_chat_id, "❌ Invalid link format")
            return
        
        await bot_client.send_message(notify_chat_id, f"🔍 Fetching message...")
        
        # Get the message
        message = await user_client.get_messages(channel, ids=msg_id)
        
        if not message:
            await bot_client.send_message(notify_chat_id, "❌ Message not found or no access")
            return
        
        if not message.media:
            await bot_client.send_message(notify_chat_id, "❌ No media in this message")
            return
        
        await bot_client.send_message(notify_chat_id, f"⬇️ Starting download...")
        result = await download_media(message)
        
        if result:
            await bot_client.send_message(notify_chat_id, f"✅ Downloaded successfully!\n📁 File: {os.path.basename(result)}")
        else:
            await bot_client.send_message(notify_chat_id, "❌ Download failed")
            
    except Exception as e:
        logger.error(f"Error in download_single: {str(e)}")
        await bot_client.send_message(notify_chat_id, f"❌ Error: {str(e)}")

async def download_batch(start_link, end_link, notify_chat_id):
    try:
        channel_start, msg_id_start = parse_message_link(start_link)
        channel_end, msg_id_end = parse_message_link(end_link)
        
        if not all([channel_start, msg_id_start, channel_end, msg_id_end]):
            await bot_client.send_message(notify_chat_id, "❌ Invalid link format")
            return
        
        if channel_start != channel_end:
            await bot_client.send_message(notify_chat_id, "❌ Both links must be from the same channel")
            return
        
        # Ensure start < end
        if msg_id_start > msg_id_end:
            msg_id_start, msg_id_end = msg_id_end, msg_id_start
        
        total = msg_id_end - msg_id_start + 1
        await bot_client.send_message(
            notify_chat_id, 
            f"📦 Starting batch download\nChannel: {channel_start}\nMessages: {msg_id_start} to {msg_id_end}\nTotal: {total}"
        )
        
        downloaded = 0
        failed = 0
        skipped = 0
        
        for msg_id in range(msg_id_start, msg_id_end + 1):
            try:
                message = await user_client.get_messages(channel_start, ids=msg_id)
                
                if message and message.media:
                    result = await download_media(message)
                    if result:
                        downloaded += 1
                    else:
                        failed += 1
                else:
                    skipped += 1
                
                # Update progress every 5 messages
                if (msg_id - msg_id_start + 1) % 5 == 0:
                    await bot_client.send_message(
                        notify_chat_id,
                        f"📊 Progress: {msg_id - msg_id_start + 1}/{total}\n✅ Downloaded: {downloaded}\n❌ Failed: {failed}\n⏭️ Skipped: {skipped}"
                    )
                
                # Small delay to avoid rate limits
                await asyncio.sleep(2)
                
            except Exception as e:
                logger.error(f"Error downloading message {msg_id}: {str(e)}")
                failed += 1
        
        await bot_client.send_message(
            notify_chat_id,
            f"🎉 Batch download complete!\n✅ Downloaded: {downloaded}\n❌ Failed: {failed}\n⏭️ Skipped: {skipped}\n📁 Path: {DOWNLOAD_PATH}"
        )
        
    except Exception as e:
        logger.error(f"Error in download_batch: {str(e)}")
        await bot_client.send_message(notify_chat_id, f"❌ Batch error: {str(e)}")

@bot_client.on(events.NewMessage(pattern='/start'))
async def start_handler(event):
    help_text = """
🤖 **Telegram Downloader Bot**

**Commands:**
- `/download <link>` - Download single message
- `/batch <start_link> <end_link>` - Download range of messages
- `/status` - Check bot status
- `/help` - Show this message

**Examples:**
`/download https://t.me/c/123456/789`
`/batch https://t.me/c/123456/100 https://t.me/c/123456/150`

**Note:** Works with both public and private channels you have access to.
    """
    await event.reply(help_text)

@bot_client.on(events.NewMessage(pattern='/download'))
async def download_handler(event):
    global is_downloading
    
    try:
        text = event.message.text
        parts = text.split(maxsplit=1)
        
        if len(parts) < 2:
            await event.reply("❌ Usage: `/download <telegram_link>`\n\nExample:\n`/download https://t.me/c/123456/789`")
            return
        
        link = parts[1].strip()
        chat_id = event.chat_id
        
        if is_downloading:
            await event.reply("⚠️ Another download is in progress. Please wait.")
            return
        
        is_downloading = True
        await download_single(link, chat_id)
        is_downloading = False
        
    except Exception as e:
        is_downloading = False
        logger.error(f"Error in download_handler: {str(e)}")
        await event.reply(f"❌ Error: {str(e)}")

@bot_client.on(events.NewMessage(pattern='/batch'))
async def batch_handler(event):
    global is_downloading
    
    try:
        text = event.message.text
        parts = text.split()
        
        if len(parts) < 3:
            await event.reply("❌ Usage: `/batch <start_link> <end_link>`\n\nExample:\n`/batch https://t.me/c/123456/100 https://t.me/c/123456/150`")
            return
        
        start_link = parts[1].strip()
        end_link = parts[2].strip()
        chat_id = event.chat_id
        
        if is_downloading:
            await event.reply("⚠️ Another download is in progress. Please wait.")
            return
        
        is_downloading = True
        await download_batch(start_link, end_link, chat_id)
        is_downloading = False
        
    except Exception as e:
        is_downloading = False
        logger.error(f"Error in batch_handler: {str(e)}")
        await event.reply(f"❌ Error: {str(e)}")

@bot_client.on(events.NewMessage(pattern='/status'))
async def status_handler(event):
    status = "🟢 Idle" if not is_downloading else "🔴 Downloading..."
    await event.reply(f"""
📊 **Bot Status**

Status: {status}
Download Path: `{DOWNLOAD_PATH}`
Session: {'String' if SESSION_STRING else 'File'}

Use /help for available commands.
    """)

@bot_client.on(events.NewMessage(pattern='/help'))
async def help_handler(event):
    await start_handler(event)

async def main():
    try:
        logger.info("Starting user client...")
        if SESSION_STRING:
            await user_client.start()
        else:
            await user_client.start(phone=PHONE)
        logger.info("✅ User client started")
        
        logger.info("Starting bot client...")
        await bot_client.start(bot_token=BOT_TOKEN)
        logger.info("✅ Bot client started")
        
        logger.info("🚀 Bot is running and ready!")
        
        # Keep the bot running
        await bot_client.run_until_disconnected()
        
    except Exception as e:
        logger.error(f"Error in main: {str(e)}")
        raise
    finally:
        await user_client.disconnect()
        await bot_client.disconnect()

if __name__ == '__main__':
    asyncio.run(main())
