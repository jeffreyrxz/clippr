import os
import json
import uuid
import threading
import subprocess
import tempfile
import re
import traceback
from pathlib import Path
from flask import Flask, render_template, request, jsonify, send_file

app = Flask(__name__)

BASE_DIR = Path(__file__).parent
DOWNLOADS_DIR = BASE_DIR / "downloads"
CLIPS_DIR = BASE_DIR / "clips"
DOWNLOADS_DIR.mkdir(exist_ok=True)
CLIPS_DIR.mkdir(exist_ok=True)

# In-memory job store
jobs = {}

def run_job(job_id, video_url, api_key, num_clips, clip_duration, double_speed=False):
    log_path = BASE_DIR / f"{job_id}.log"

    def log(msg):
        line = f"[{job_id}] {msg}"
        print(line, flush=True)
        with open(log_path, "a") as lf:
            lf.write(line + "\n")

    def update(status, message, progress=None, data=None):
        jobs[job_id]["status"] = status
        jobs[job_id]["message"] = message
        if progress is not None:
            jobs[job_id]["progress"] = progress
        if data:
            jobs[job_id].update(data)

    try:
        log("── Job started ──────────────────────────────────")

        # ── Step 1: Download video ────────────────────────────────────────────
        update("running", "Downloading video...", 10)
        video_path = DOWNLOADS_DIR / f"{job_id}.%(ext)s"
        log(f"Downloading: {video_url}")
        result = subprocess.run(
            [
                "yt-dlp",
                "-f", "bestvideo[height<=720][ext=mp4]+bestaudio[ext=m4a]/bestvideo[height<=720]+bestaudio/best[height<=720]",
                "--no-playlist",
                "-o", str(video_path),
                "--merge-output-format", "mp4",
                video_url,
            ],
            capture_output=True, text=True, timeout=300
        )
        log(f"yt-dlp returncode: {result.returncode}")
        if result.returncode != 0:
            log(f"yt-dlp stderr:\n{result.stderr[-1000:]}")
            raise RuntimeError(f"Download failed: {result.stderr[-500:]}")
        log(f"yt-dlp stdout tail:\n{result.stdout[-300:]}")

        # Find the downloaded file
        downloaded = list(DOWNLOADS_DIR.glob(f"{job_id}.*"))
        if not downloaded:
            raise RuntimeError("Downloaded file not found.")
        video_file = downloaded[0]
        log(f"Downloaded file: {video_file} ({video_file.stat().st_size // 1024} KB)")

        # Quick sanity check — does it have a video stream?
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=codec_type", "-of", "csv=p=0", str(video_file)],
            capture_output=True, text=True
        )
        has_video = "video" in probe.stdout
        log(f"Has video stream: {has_video} | ffprobe stdout: {probe.stdout.strip()!r}")
        if not has_video:
            raise RuntimeError(
                "Downloaded file has no video stream — yt-dlp only grabbed audio. "
                "Check the URL and yt-dlp format string."
            )

        # ── Step 2: Extract audio for transcription ───────────────────────────
        update("running", "Extracting audio...", 25)
        log("Extracting audio...")
        audio_path = DOWNLOADS_DIR / f"{job_id}.wav"
        audio_result = subprocess.run(
            ["ffmpeg", "-y", "-i", str(video_file),
             "-ar", "16000", "-ac", "1", "-vn", str(audio_path)],
            capture_output=True, timeout=300
        )
        if audio_result.returncode != 0:
            log(f"ffmpeg audio extract stderr:\n{audio_result.stderr.decode()[-500:]}")
            raise RuntimeError("Audio extraction failed.")
        log(f"Audio extracted: {audio_path.stat().st_size // 1024} KB")

        # ── Step 3: Transcribe with Whisper ───────────────────────────────────
        update("running", "Transcribing audio (this takes a minute)...", 40)
        log("Starting Whisper transcription...")
        transcript_path = DOWNLOADS_DIR / f"{job_id}.json"
        result = subprocess.run(
            ["python3", "-c", f"""
import whisper, json
model = whisper.load_model("base")
result = model.transcribe("{audio_path}", word_timestamps=False)
with open("{transcript_path}", "w") as f:
    json.dump(result, f)
print("done")
"""],
            capture_output=True, text=True, timeout=600
        )
        log(f"Whisper returncode: {result.returncode}")
        if result.returncode != 0:
            log(f"Whisper stderr:\n{result.stderr[-1000:]}")
            raise RuntimeError(f"Transcription failed: {result.stderr[-500:]}")
        log(f"Whisper stdout: {result.stdout.strip()}")

        with open(transcript_path) as f:
            transcript_data = json.load(f)

        # Build a readable transcript with timestamps
        segments = transcript_data.get("segments", [])
        transcript_text = ""
        for seg in segments:
            start = seg["start"]
            end = seg["end"]
            text = seg["text"].strip()
            transcript_text += f"[{start:.1f}s - {end:.1f}s] {text}\n"

        if not transcript_text.strip():
            transcript_text = transcript_data.get("text", "No transcript available.")

        # ── Step 4: Ask Claude to find viral moments ───────────────────────────
        update("running", "Asking Claude to find viral moments...", 65)
        log(f"Transcript segments: {len(segments)} | chars sent to Claude: {len(transcript_text[:12000])}")
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)

        prompt = f"""You are an expert TikTok clip editor who cuts viral clips from streamer and YouTube content.

I have a transcript with timestamps. You MUST select exactly {num_clips} clips. No exceptions — always return exactly {num_clips} clips even if the content seems low energy. Pick the best moments available.

Look for:
- Reactions, hype, laughter, disbelief, anger
- Funny or quotable moments
- Anything surprising or unexpected
- Story beats, reveals, or confrontations
- High-energy or loud moments

Each clip should be approximately {clip_duration} seconds long. Make start and end timestamps precise to the second.

IMPORTANT: You MUST return exactly {num_clips} clips. Never return an empty array.

TRANSCRIPT:
{transcript_text[:12000]}

Respond ONLY with valid JSON. No markdown, no explanation, no code fences. Example format:
{{"clips": [{{"start": 10.5, "end": 55.0, "title": "CLIP TITLE", "reason": "Why this will blow up", "hook": "Opening line that hooks viewers"}}]}}"""

        message = client.messages.create(
            model="claude-opus-4-5",
            max_tokens=1500,
            messages=[{"role": "user", "content": prompt}]
        )

        raw = message.content[0].text.strip()
        log(f"Claude raw response (first 500 chars):\n{raw[:500]}")
        # Strip markdown fences if present
        raw = re.sub(r"^```[a-z]*\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
        raw = raw.strip()
        clip_data = json.loads(raw)
        suggested_clips = clip_data["clips"]
        log(f"Claude suggested {len(suggested_clips)} clips")

        # ── Step 5: Cut clips with ffmpeg ─────────────────────────────────────
        update("running", f"Cutting {len(suggested_clips)} clips...", 80)
        log(f"Cutting {len(suggested_clips)} clips from {video_file.name}")
        job_clips_dir = CLIPS_DIR / job_id
        job_clips_dir.mkdir(exist_ok=True)

        cut_clips = []
        for i, clip in enumerate(suggested_clips):
            start = max(0, float(clip["start"]))
            end = float(clip["end"])
            duration = end - start
            log(f"Clip {i+1}: {start:.1f}s → {end:.1f}s (duration={duration:.1f}s)")
            if duration <= 0:
                log(f"Clip {i+1}: SKIPPED — zero/negative duration")
                continue

            out_path = job_clips_dir / f"clip_{i+1}.mp4"
            ffmpeg_cmd = [
                "ffmpeg", "-y",
                "-ss", str(start),
                "-i", str(video_file),
                "-t", str(duration),
                # Fit full clip inside 9:16 frame, black bars fill empty space
                "-vf", ("scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2:black,setpts=0.5*PTS"
                        if double_speed else
                        "scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2:black"),
                "-af", "atempo=2.0" if double_speed else "anull",
                "-c:v", "libx264", "-crf", "23", "-preset", "fast",
                "-c:a", "aac", "-b:a", "128k",
                str(out_path)
            ]
            log(f"Running ffmpeg for clip {i+1}...")
            ffmpeg_result = subprocess.run(ffmpeg_cmd, capture_output=True, timeout=120)
            log(f"Clip {i+1} ffmpeg returncode: {ffmpeg_result.returncode}")

            if ffmpeg_result.returncode != 0:
                log(f"Clip {i+1} ffmpeg stderr:\n{ffmpeg_result.stderr.decode()[-800:]}")
            elif not out_path.exists():
                log(f"Clip {i+1}: ffmpeg succeeded but output file missing!")

            if ffmpeg_result.returncode == 0 and out_path.exists():
                cut_clips.append({
                    "index": i + 1,
                    "title": clip.get("title", f"Clip {i+1}"),
                    "reason": clip.get("reason", ""),
                    "hook": clip.get("hook", ""),
                    "start": start,
                    "end": end,
                    "filename": f"clip_{i+1}.mp4",
                    "job_id": job_id,
                    "size_mb": round(out_path.stat().st_size / 1024 / 1024, 1)
                })

        # ── Cleanup source files ───────────────────────────────────────────────
        for f in [video_file, audio_path, transcript_path]:
            try:
                f.unlink(missing_ok=True)
            except Exception:
                pass

        log(f"── Done: {len(cut_clips)} clips ready ─────────────")
        update("done", f"Done! {len(cut_clips)} clips ready.", 100, {"clips": cut_clips})

    except Exception as e:
        log(f"── JOB FAILED ──────────────────────────────────────")
        traceback.print_exc()
        jobs[job_id]["status"] = "error"
        jobs[job_id]["message"] = str(e)


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/start", methods=["POST"])
def start():
    data = request.json
    video_url = data.get("url", "").strip()
    api_key = data.get("api_key", "").strip()
    num_clips = int(data.get("num_clips", 5))
    clip_duration = int(data.get("clip_duration", 45))
    double_speed = bool(data.get("double_speed", False))

    if not video_url or not api_key:
        return jsonify({"error": "URL and API key are required."}), 400

    job_id = str(uuid.uuid4())[:8]
    jobs[job_id] = {"status": "running", "message": "Starting...", "progress": 0, "clips": []}

    t = threading.Thread(target=run_job, args=(job_id, video_url, api_key, num_clips, clip_duration, double_speed), daemon=True)
    t.start()

    return jsonify({"job_id": job_id})


@app.route("/status/<job_id>")
def status(job_id):
    job = jobs.get(job_id)
    if not job:
        return jsonify({"error": "Job not found"}), 404
    return jsonify(job)


@app.route("/download/<job_id>/<filename>")
def download(job_id, filename):
    clip_path = CLIPS_DIR / job_id / filename
    if not clip_path.exists():
        return jsonify({"error": "File not found"}), 404
    return send_file(str(clip_path), as_attachment=True, download_name=filename)


# Serve the log file for a job (debug)
@app.route("/log/<job_id>")
def get_log(job_id):
    log_path = BASE_DIR / f"{job_id}.log"
    if not log_path.exists():
        return "No log yet.", 404
    return log_path.read_text(), 200, {"Content-Type": "text/plain"}


if __name__ == "__main__":
    print("\n🎬  Viral Clipper is running at http://localhost:8080\n")
    app.run(debug=False, port=8080)
