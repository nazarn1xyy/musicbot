import asyncio
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import (
    InlineKeyboardMarkup, 
    InlineKeyboardButton,
    FSInputFile
)

from config import BOT_TOKEN
from music_service import search_songs, download_song, cleanup_file, get_lyrics


bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# Cache for storing file_ids of already uploaded songs
audio_cache: dict[str, str] = {}

# Cache for storing song metadata (title, artist, thumbnail)
song_metadata: dict[str, tuple[str, str, str]] = {}

# Cache for search results (user_id -> (query, all_results))
search_cache: dict[int, tuple[str, list]] = {}

# Pagination settings
SONGS_PER_PAGE = 5
TOTAL_SEARCH_RESULTS = 20

# Download queue settings
MAX_CONCURRENT_DOWNLOADS = 3
download_semaphore = asyncio.Semaphore(MAX_CONCURRENT_DOWNLOADS)
download_queue: list[str] = []


async def process_download(callback: types.CallbackQuery, video_id: str, title: str, artist: str, thumbnail: str):
    """Process a single download with queue management"""
    download_queue.append(video_id)
    queue_position = len(download_queue)
    
    if queue_position > MAX_CONCURRENT_DOWNLOADS:
        await callback.answer(f"📋 В очереди: #{queue_position - MAX_CONCURRENT_DOWNLOADS}")
    else:
        await callback.answer("⏳ Скачиваю...")
    
    async with download_semaphore:
        filepath = await download_song(video_id, title, artist, thumbnail)
        
        if video_id in download_queue:
            download_queue.remove(video_id)
        
        if not filepath:
            await callback.message.answer("❌ Не удалось скачать. Попробуй другую песню.")
            return
        
        thumb_path = None
        try:
            audio_file = FSInputFile(filepath)
            
            # Download thumbnail for Telegram
            if thumbnail:
                import requests
                import tempfile
                try:
                    resp = requests.get(thumbnail, timeout=10)
                    if resp.status_code == 200:
                        thumb_path = tempfile.NamedTemporaryFile(suffix='.jpg', delete=False).name
                        with open(thumb_path, 'wb') as f:
                            f.write(resp.content)
                except:
                    pass
            
            if thumb_path:
                thumb_file = FSInputFile(thumb_path)
                sent_message = await callback.message.answer_audio(
                    audio=audio_file,
                    thumbnail=thumb_file,
                    title=title,
                    performer=artist
                )
            else:
                sent_message = await callback.message.answer_audio(
                    audio=audio_file,
                    title=title,
                    performer=artist
                )
            
            if sent_message.audio:
                audio_cache[video_id] = sent_message.audio.file_id
                
        finally:
            cleanup_file(filepath)
            if thumb_path:
                import os
                try:
                    os.remove(thumb_path)
                except:
                    pass


def build_results_keyboard(songs: list, page: int, user_id: int) -> InlineKeyboardMarkup:
    """Build keyboard with song results and pagination"""
    start_idx = page * SONGS_PER_PAGE
    end_idx = start_idx + SONGS_PER_PAGE
    page_songs = songs[start_idx:end_idx]
    
    buttons = []
    for song in page_songs:
        song_metadata[song.video_id] = (song.title, song.artist, song.thumbnail or "")
        
        button_text = f"{song.artist} • {song.title}"
        if len(button_text) > 55:
            button_text = button_text[:52] + "..."
        
        # Row with download and lyrics buttons
        buttons.append([
            InlineKeyboardButton(
                text=button_text,
                callback_data=f"dl:{song.video_id}"
            ),
            InlineKeyboardButton(
                text="📝",
                callback_data=f"lyrics:{song.video_id}"
            )
        ])
    
    # Pagination row
    total_pages = (len(songs) + SONGS_PER_PAGE - 1) // SONGS_PER_PAGE
    pagination_buttons = []
    
    if page > 0:
        pagination_buttons.append(
            InlineKeyboardButton(text="⬅️", callback_data=f"page:{page - 1}")
        )
    
    pagination_buttons.append(
        InlineKeyboardButton(text=f"{page + 1}/{total_pages}", callback_data="noop")
    )
    
    if page < total_pages - 1:
        pagination_buttons.append(
            InlineKeyboardButton(text="➡️", callback_data=f"page:{page + 1}")
        )
    
    if pagination_buttons:
        buttons.append(pagination_buttons)
    
    return InlineKeyboardMarkup(inline_keyboard=buttons)


@dp.message(Command("start"))
async def start_handler(message: types.Message):
    """Handle /start command"""
    await message.answer(
        "🎵 <b>Music Bot</b>\n\n"
        "Отправь мне название песни — я найду и скачаю её для тебя!\n\n"
        "� — скачать трек\n"
        "📝 — показать текст песни",
        parse_mode="HTML"
    )


@dp.message(Command("queue"))
async def queue_handler(message: types.Message):
    """Show current queue status"""
    active = min(len(download_queue), MAX_CONCURRENT_DOWNLOADS)
    waiting = max(0, len(download_queue) - MAX_CONCURRENT_DOWNLOADS)
    
    await message.answer(
        f"📊 <b>Статус очереди</b>\n\n"
        f"⬇️ Скачивается: {active}/{MAX_CONCURRENT_DOWNLOADS}\n"
        f"⏳ В ожидании: {waiting}",
        parse_mode="HTML"
    )


@dp.message(F.text & ~F.text.startswith("/"))
async def search_handler(message: types.Message):
    """Handle text messages - search for songs"""
    query = message.text.strip()
    if not query:
        return
    
    # Delete user's message
    try:
        await message.delete()
    except:
        pass
    
    status_msg = await message.answer("🔍 Ищу...")
    
    songs = search_songs(query, limit=TOTAL_SEARCH_RESULTS)
    
    if not songs:
        await status_msg.edit_text("❌ Ничего не найдено. Попробуй другой запрос.")
        return
    
    # Cache search results for pagination
    search_cache[message.from_user.id] = (query, songs)
    
    keyboard = build_results_keyboard(songs, page=0, user_id=message.from_user.id)
    
    await status_msg.edit_text(
        f"🎵 Результаты: <b>{query}</b>",
        reply_markup=keyboard,
        parse_mode="HTML"
    )


@dp.callback_query(F.data.startswith("page:"))
async def pagination_handler(callback: types.CallbackQuery):
    """Handle pagination buttons"""
    page = int(callback.data.split(":")[1])
    user_id = callback.from_user.id
    
    if user_id not in search_cache:
        await callback.answer("⚠️ Поиск устарел, введи запрос заново")
        return
    
    query, songs = search_cache[user_id]
    keyboard = build_results_keyboard(songs, page=page, user_id=user_id)
    
    await callback.message.edit_reply_markup(reply_markup=keyboard)
    await callback.answer()


@dp.callback_query(F.data == "noop")
async def noop_handler(callback: types.CallbackQuery):
    """Handle page indicator click (do nothing)"""
    await callback.answer()


@dp.callback_query(F.data.startswith("lyrics:"))
async def lyrics_handler(callback: types.CallbackQuery):
    """Show lyrics for a song"""
    video_id = callback.data.split(":")[1]
    
    await callback.answer("📝 Загружаю текст...")
    
    # Get song info
    title, artist, _ = song_metadata.get(video_id, ("", "", ""))
    
    # Get lyrics in thread pool
    loop = asyncio.get_event_loop()
    lyrics = await loop.run_in_executor(None, get_lyrics, video_id)
    
    if not lyrics:
        await callback.message.answer(
            f"📝 <b>{artist} - {title}</b>\n\n"
            "❌ Текст песни не найден",
            parse_mode="HTML"
        )
        return
    
    # Truncate if too long for Telegram (4096 chars limit)
    header = f"📝 <b>{artist} - {title}</b>\n\n"
    max_lyrics_len = 4000 - len(header)
    
    if len(lyrics) > max_lyrics_len:
        lyrics = lyrics[:max_lyrics_len] + "..."
    
    await callback.message.answer(
        f"{header}{lyrics}",
        parse_mode="HTML"
    )


@dp.callback_query(F.data.startswith("dl:"))
async def download_handler(callback: types.CallbackQuery):
    """Handle download button press"""
    video_id = callback.data.split(":")[1]
    
    if video_id in audio_cache:
        await callback.message.answer_audio(audio=audio_cache[video_id])
        await callback.answer("✅ Из кэша")
        return
    
    title, artist, thumbnail = song_metadata.get(video_id, ("", "", ""))
    asyncio.create_task(process_download(callback, video_id, title, artist, thumbnail))


async def main():
    """Start the bot"""
    print("🎵 Music Bot started!")
    print(f"📋 Max concurrent downloads: {MAX_CONCURRENT_DOWNLOADS}")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
