# CLIPPR — AI Viral Clip Generator

Paste a YouTube or stream link → Claude reads the transcript → ffmpeg cuts TikTok-ready vertical clips.

## Quick Start

### 1. Prerequisites
- Python 3.8+
- ffmpeg installed (`brew install ffmpeg` on Mac, `sudo apt install ffmpeg` on Linux)
- An Anthropic API key → get one at console.anthropic.com

### 2. Install
```bash
bash setup.sh
```

### 3. Run
```bash
python3 app.py
```
Open **http://localhost:5000** in your browser.

---

## How It Works

1. **Download** — yt-dlp grabs the video (YouTube, Twitch VODs, Kick, etc.)
2. **Transcribe** — OpenAI Whisper (runs locally, free) converts speech to text with timestamps
3. **Analyze** — Claude reads the transcript and picks the N most viral-worthy moments
4. **Cut** — ffmpeg clips those moments and crops to 9:16 vertical for TikTok
5. **Download** — grab your clips from the browser

## Notes

- First run downloads the Whisper model (~140MB for "base"). Faster models: change `"base"` to `"tiny"` in app.py for speed, `"small"` or `"medium"` for better accuracy.
- Clips are saved in the `clips/` folder.
- The app runs fully locally — your API key is never stored.
- Works with YouTube, Twitch VODs, Kick VODs, and most public video URLs.

## ⚠️ Legal

Get permission from creators before posting their clips commercially. This tool is for personal/educational use.
