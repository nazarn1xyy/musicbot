import os
import asyncio
from typing import Optional
from dataclasses import dataclass

from ytmusicapi import YTMusic
import yt_dlp

from config import DOWNLOAD_PATH


@dataclass
class Song:
    """Song data from search results"""
    video_id: str
    title: str
    artist: str
    duration: str
    thumbnail: Optional[str] = None


# Initialize YTMusic client (no auth needed for search)
ytmusic = YTMusic()


def search_songs(query: str, limit: int = 10) -> list[Song]:
    """
    Search for songs on YouTube Music
    
    Args:
        query: Search query
        limit: Maximum number of results
        
    Returns:
        List of Song objects
    """
    results = ytmusic.search(query, filter="songs", limit=limit)
    songs = []
    
    for item in results:
        if item.get("resultType") != "song":
            continue
            
        # Get artist names
        artists = item.get("artists", [])
        artist_name = ", ".join([a.get("name", "") for a in artists]) if artists else "Unknown"
        
        # Get duration
        duration = item.get("duration", "0:00")
        
        # Get thumbnail
        thumbnails = item.get("thumbnails", [])
        thumbnail = thumbnails[-1].get("url") if thumbnails else None
        
        songs.append(Song(
            video_id=item.get("videoId", ""),
            title=item.get("title", "Unknown"),
            artist=artist_name,
            duration=duration,
            thumbnail=thumbnail
        ))
    
    return songs


def get_lyrics(video_id: str) -> Optional[str]:
    """
    Get lyrics for a song
    
    Args:
        video_id: YouTube video ID
        
    Returns:
        Lyrics text or None if not available
    """
    try:
        # Get watch playlist to find lyrics browse id
        watch_playlist = ytmusic.get_watch_playlist(video_id)
        lyrics_browse_id = watch_playlist.get("lyrics")
        
        if not lyrics_browse_id:
            return None
        
        # Get lyrics
        lyrics_data = ytmusic.get_lyrics(lyrics_browse_id)
        if lyrics_data:
            return lyrics_data.get("lyrics")
        
        return None
    except Exception as e:
        print(f"Lyrics error: {e}")
        return None


def sanitize_filename(name: str) -> str:
    """Remove invalid characters from filename"""
    invalid_chars = '<>:"/\\|?*'
    for char in invalid_chars:
        name = name.replace(char, '')
    return name.strip()


async def download_song(video_id: str, title: str = "", artist: str = "", thumbnail: str = "") -> Optional[str]:
    """
    Download song from YouTube as MP3
    
    Args:
        video_id: YouTube video ID
        title: Song title for filename
        artist: Artist name for filename
        thumbnail: URL to thumbnail image
        
    Returns:
        Path to downloaded MP3 file or None if failed
    """
    url = f"https://music.youtube.com/watch?v={video_id}"
    
    # Create proper filename
    if title and artist:
        filename = sanitize_filename(f"{artist} - {title}")
    elif title:
        filename = sanitize_filename(title)
    else:
        filename = video_id
    
    output_template = os.path.join(DOWNLOAD_PATH, f"{filename}.%(ext)s")
    final_path = os.path.join(DOWNLOAD_PATH, f"{filename}.mp3")
    
    ydl_opts = {
        'format': 'bestaudio/best',
        'outtmpl': output_template,
        'postprocessors': [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
            'preferredquality': '192',
        }],
        'quiet': True,
        'no_warnings': True,
    }
    
    def _download():
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([url])
            
            # Set proper ID3 tags
            if os.path.exists(final_path) and (title or artist or thumbnail):
                try:
                    from mutagen.mp3 import MP3
                    from mutagen.id3 import ID3, TIT2, TPE1, APIC, ID3NoHeaderError
                    import requests
                    
                    try:
                        audio = MP3(final_path, ID3=ID3)
                    except ID3NoHeaderError:
                        audio = MP3(final_path)
                        audio.add_tags()
                    
                    if title:
                        audio.tags.add(TIT2(encoding=3, text=title))
                    if artist:
                        audio.tags.add(TPE1(encoding=3, text=artist))
                    
                    # Download and embed thumbnail
                    if thumbnail:
                        print(f"Downloading thumbnail: {thumbnail[:80]}...")
                        try:
                            response = requests.get(thumbnail, timeout=10)
                            print(f"Thumbnail response: {response.status_code}, size: {len(response.content)} bytes")
                            if response.status_code == 200 and len(response.content) > 0:
                                audio.tags.add(APIC(
                                    encoding=3,
                                    mime='image/jpeg',
                                    type=3,  # Cover (front)
                                    desc='Cover',
                                    data=response.content
                                ))
                                print("✅ Thumbnail embedded successfully")
                        except Exception as e:
                            print(f"Thumbnail download error: {e}")
                    else:
                        print("⚠️ No thumbnail URL provided")
                    
                    audio.save()
                    print("✅ ID3 tags saved")
                except Exception as e:
                    print(f"ID3 tag error: {e}")
            
            return final_path
        except Exception as e:
            print(f"Download error: {e}")
            return None
    
    # Run download in thread pool to not block async
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(None, _download)
    
    if result and os.path.exists(result):
        return result
    return None


def cleanup_file(filepath: str) -> None:
    """Remove temporary file"""
    try:
        if os.path.exists(filepath):
            os.remove(filepath)
    except Exception as e:
        print(f"Cleanup error: {e}")
