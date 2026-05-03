#!/usr/bin/env python3
"""
YouTube Transcript Extractor Server
Runs on localhost:8002
"""

from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
import sys
import os
import re
import json
import urllib.request
import urllib.error
import urllib.parse
import subprocess
import uuid
from datetime import datetime
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Add parent directory to path to import the formatter
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

app = Flask(__name__)
CORS(app)

OPENAI_API_KEY = os.getenv('OPENAI_API_KEY')
TRANSCRIPTION_MODEL = os.getenv('OPENAI_TRANSCRIPTION_MODEL', 'whisper-1')
OPENAI_CHUNKING_MODEL = os.getenv('OPENAI_CHUNKING_MODEL', 'gpt-4o-mini')
YTDLP_COOKIES_FROM_BROWSER = os.getenv('YTDLP_COOKIES_FROM_BROWSER')
YTDLP_COOKIES_FILE = os.getenv('YTDLP_COOKIES_FILE')
PREVIEW_AUDIO_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'preview_audio')

def is_youtube_url(url):
    """Check whether the URL is a YouTube URL."""
    host = urllib.parse.urlparse(url).netloc.lower()
    return 'youtube.com' in host or 'youtu.be' in host

def extract_video_id(url):
    """Extract video ID from YouTube URL (including short URLs)"""
    patterns = [
        # Standard YouTube watch URLs
        r'youtube\.com\/watch\?.*v=([a-zA-Z0-9_-]{11})',
        # Short youtu.be URLs
        r'youtu\.be\/([a-zA-Z0-9_-]{11})',
        # Embed URLs
        r'youtube\.com\/embed\/([a-zA-Z0-9_-]{11})',
        # Shorts URLs
        r'youtube\.com\/shorts\/([a-zA-Z0-9_-]{11})',
        # Short URLs with parameters
        r'youtu\.be\/([a-zA-Z0-9_-]{11})\?',
    ]
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1)

    return None

def get_transcript(video_id):
    """Extract transcript using youtube-transcript-api with timestamps"""
    try:
        from youtube_transcript_api import YouTubeTranscriptApi

        api = YouTubeTranscriptApi()
        transcript_list = api.list(video_id)

        # Try to get English transcript
        try:
            transcript_obj = transcript_list.find_transcript(['en', 'en-US', 'en-GB'])
        except:
            # If no English, get the first available
            transcript_obj = list(transcript_list)[0]

        # Fetch the transcript with full data (text, start, duration)
        transcript_data = transcript_obj.fetch()

        # Convert FetchedTranscriptSnippet objects to dictionaries
        transcript_dict = []
        for entry in transcript_data:
            transcript_dict.append({
                'text': entry.text,
                'start': entry.start,
                'duration': entry.duration
            })

        # Return both text and timestamp data
        raw_text = '\n'.join([entry['text'] for entry in transcript_dict])
        return raw_text, transcript_dict

    except Exception as e:
        raise Exception(f"Failed to extract transcript: {str(e)}")

def format_with_openai(transcript_text):
    """Format transcript using OpenAI API"""
    url = "https://api.openai.com/v1/chat/completions"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {OPENAI_API_KEY}"
    }

    data = {
        "model": "gpt-4o-mini",
        "messages": [
            {
                "role": "system",
                "content": "You are a transcript formatter. Your task is to format transcripts with proper punctuation, paragraph breaks, and sentence structure while preserving ALL original content exactly. Do not add, remove, or change any words - only add punctuation (periods, question marks, commas) and organize into logical paragraphs. Keep speaker turns separate where obvious. Fix obvious typos from auto-captions (e.g., 'maau' -> 'Macau', 'orts' -> 'sports')."
            },
            {
                "role": "user",
                "content": f"""Please format this transcript properly with correct punctuation, paragraph breaks, and sentence structure. Keep ALL original content - do not add, remove, or change any words. Only add punctuation and organize into readable paragraphs:\n\n{transcript_text}"""
            }
        ],
        "temperature": 0.3,
        "max_tokens": 4000
    }

    try:
        req = urllib.request.Request(url, data=json.dumps(data).encode('utf-8'), headers=headers)
        with urllib.request.urlopen(req, timeout=120) as response:
            result = json.loads(response.read().decode('utf-8'))
            return result['choices'][0]['message']['content']
    except urllib.error.HTTPError as e:
        raise Exception(f"OpenAI API error: {e.code} - {e.reason}")
    except Exception as e:
        raise Exception(f"Formatting failed: {str(e)}")

def format_timestamp(seconds):
    """Convert seconds to MM:SS format"""
    minutes = int(seconds // 60)
    secs = int(seconds % 60)
    return f"{minutes:02d}:{secs:02d}"

def safe_file_stem(name, default='media'):
    """Create a safe filename stem for saved study assets."""
    safe_name = re.sub(r'[^\w\s-]', '', name or '').strip().replace(' ', '_')
    return safe_name or default

def get_desktop_path():
    """Return Desktop path for saved outputs."""
    return os.path.expanduser("~/Desktop")

def run_media_command(cmd, timeout, action):
    """Run a media command and raise a useful error if it fails."""
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError as e:
        tool = cmd[0] if cmd else 'media tool'
        raise Exception(f"{tool} is not installed or not available in PATH") from e
    except subprocess.TimeoutExpired:
        raise Exception(f"{action} timed out")

    if result.returncode != 0:
        details = (result.stderr or result.stdout or '').strip()
        raise Exception(f"{action} failed: {details}")

    return result

def add_ytdlp_auth_args(cmd):
    """Add optional yt-dlp cookie auth for sites like Instagram."""
    if YTDLP_COOKIES_FROM_BROWSER:
        cmd.extend(['--cookies-from-browser', YTDLP_COOKIES_FROM_BROWSER])
    elif YTDLP_COOKIES_FILE:
        cmd.extend(['--cookies', YTDLP_COOKIES_FILE])
    return cmd

def download_full_audio_file(video_url, output_filename):
    """Download audio from YouTube, Instagram, or other yt-dlp supported media URLs."""
    output_base = os.path.splitext(output_filename)[0]
    output_template = f"{output_base}.%(ext)s"
    expected_output = f"{output_base}.mp3"
    cmd = [
        'yt-dlp',
        '--no-playlist',
        '-x',
        '--audio-format', 'mp3',
        '--audio-quality', '0',
        '-o', output_template,
        video_url
    ]
    add_ytdlp_auth_args(cmd)
    run_media_command(cmd, 300, 'Audio download')
    return expected_output if os.path.exists(expected_output) else output_filename

def build_multipart_form(fields, file_field, file_path):
    """Build a multipart/form-data body for OpenAI audio transcription."""
    boundary = f"----TranscriptExtractor{uuid.uuid4().hex}"
    body = bytearray()

    for name, value in fields:
        body.extend(f"--{boundary}\r\n".encode('utf-8'))
        body.extend(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode('utf-8'))
        body.extend(str(value).encode('utf-8'))
        body.extend(b"\r\n")

    file_name = os.path.basename(file_path)
    body.extend(f"--{boundary}\r\n".encode('utf-8'))
    body.extend(
        f'Content-Disposition: form-data; name="{file_field}"; filename="{file_name}"\r\n'.encode('utf-8')
    )
    body.extend(b"Content-Type: audio/mpeg\r\n\r\n")
    with open(file_path, 'rb') as f:
        body.extend(f.read())
    body.extend(b"\r\n")
    body.extend(f"--{boundary}--\r\n".encode('utf-8'))

    return bytes(body), boundary

def transcribe_audio_with_openai(audio_path):
    """Transcribe audio and return raw text plus timestamped segments when available."""
    if not OPENAI_API_KEY:
        raise Exception("OPENAI_API_KEY is required for audio transcription")

    fields = [
        ('model', TRANSCRIPTION_MODEL),
        ('language', 'en'),
        ('response_format', 'verbose_json'),
        ('timestamp_granularities[]', 'segment')
    ]
    body, boundary = build_multipart_form(fields, 'file', audio_path)

    headers = {
        'Authorization': f'Bearer {OPENAI_API_KEY}',
        'Content-Type': f'multipart/form-data; boundary={boundary}'
    }

    try:
        req = urllib.request.Request(
            'https://api.openai.com/v1/audio/transcriptions',
            data=body,
            headers=headers,
            method='POST'
        )
        with urllib.request.urlopen(req, timeout=300) as response:
            result = json.loads(response.read().decode('utf-8'))
    except urllib.error.HTTPError as e:
        error_body = e.read().decode('utf-8', errors='replace')
        raise Exception(f"OpenAI transcription error: {e.code} - {error_body}")
    except Exception as e:
        raise Exception(f"Audio transcription failed: {str(e)}")

    text = result.get('text', '').strip()
    raw_segments = result.get('segments') or []
    segments = []

    for item in raw_segments:
        start = float(item.get('start', 0))
        end = float(item.get('end', start))
        segment_text = (item.get('text') or '').strip()
        if segment_text:
            segments.append({
                'text': segment_text,
                'start': start,
                'duration': max(0, end - start)
            })

    if not segments and text:
        segments = [{
            'text': text,
            'start': 0,
            'duration': 0
        }]

    return text, segments

def create_timestamped_transcript(transcript_data):
    """Create frontend-friendly transcript segments."""
    timestamped_transcript = []
    for entry in transcript_data:
        timestamp = format_timestamp(entry['start'])
        timestamped_transcript.append({
            'timestamp': timestamp,
            'start': entry['start'],
            'duration': entry.get('duration', 0),
            'text': entry['text']
        })
    return timestamped_transcript

def group_transcript_for_study(transcript_data):
    """Merge tiny caption chunks into repeatable natural learning chunks."""
    grouped = []
    current = None
    target_min_words = 8
    target_max_words = 24
    hard_max_words = 34

    def flush_current():
        nonlocal current
        if current:
            grouped.append({
                'text': ' '.join(current['text_parts']),
                'start': current['start'],
                'duration': max(0, current['end'] - current['start'])
            })
            current = None

    def word_count(text):
        return len(re.findall(r"\b[\w']+\b", text))

    def ends_sentence(text):
        return bool(re.search(r'[.!?]["\')\]]?$', text.strip()))

    def split_embedded_sentence(entry):
        """Split a caption when two sentence-like units are inside one chunk."""
        text = (entry.get('text') or '').strip()
        match = re.match(
            r'^(.+?[.!?]["\')\]]?)(\s+[A-Z].+)$',
            text
        )
        if not match:
            return [entry]

        first_text = match.group(1).strip()
        second_text = match.group(2).strip()
        start = float(entry.get('start', 0))
        duration = float(entry.get('duration', 0) or 0)
        split_ratio = len(first_text) / max(1, len(first_text) + len(second_text))
        first_duration = duration * split_ratio

        return [
            {
                'text': first_text,
                'start': start,
                'duration': first_duration
            },
            {
                'text': second_text,
                'start': start + first_duration,
                'duration': max(0, duration - first_duration)
            }
        ]

    expanded_entries = []
    for entry in transcript_data:
        expanded_entries.extend(split_embedded_sentence(entry))

    for entry in expanded_entries:
        text = (entry.get('text') or '').strip()
        if not text:
            continue

        start = float(entry.get('start', 0))
        duration = float(entry.get('duration', 0) or 0)
        end = start + duration

        if not current:
            current = {
                'text_parts': [text],
                'start': start,
                'end': end
            }
            continue

        current_text = ' '.join(current['text_parts'])
        last_end = current['end']
        gap = start - last_end
        current_words = word_count(current_text)
        next_words = word_count(text)
        combined_words = current_words + next_words
        current_seconds = last_end - current['start']
        too_large_gap = gap > 1.2

        should_flush = (
            too_large_gap
            or current_words >= hard_max_words
            or current_seconds >= 14
            or (
                ends_sentence(current_text)
                and current_words >= target_min_words
                and combined_words > target_max_words
            )
            or (
                ends_sentence(current_text)
                and current_words >= 14
                and next_words >= 6
            )
        )

        if should_flush:
            flush_current()
            current = {
                'text_parts': [text],
                'start': start,
                'end': end
            }
        else:
            current['text_parts'].append(text)
            current['end'] = max(current['end'], end)

    flush_current()

    return grouped

def group_transcript_with_ai(transcript_data):
    """Use an LLM to merge captions into natural English-learning chunks."""
    if not OPENAI_API_KEY:
        return group_transcript_for_study(transcript_data)

    compact_entries = []
    for index, entry in enumerate(transcript_data):
        text = (entry.get('text') or '').strip()
        if not text:
            continue

        start = float(entry.get('start', 0))
        duration = float(entry.get('duration', 0) or 0)
        compact_entries.append({
            'i': index,
            's': round(start, 2),
            'e': round(start + duration, 2),
            't': text
        })

    if not compact_entries:
        return []

    # Keep very long videos on the deterministic fallback until batching is added.
    if len(compact_entries) > 220:
        return group_transcript_for_study(transcript_data)

    prompt = {
        'task': 'Group timestamped caption fragments into natural English-learning chunks.',
        'rules': [
            'Return JSON only.',
            'Do not summarize, translate, add new information, or remove important words.',
            'You may lightly fix punctuation and spacing only when needed.',
            'Each chunk should feel natural for listening/shadowing practice.',
            'Prefer one complete idea, one question, or one repeatable answer phrase per chunk.',
            'Target 8-24 words. Allow shorter for quick questions. Avoid chunks over 35 words unless necessary.',
            'Merge fragments that are clearly one sentence or one idea.',
            'Split long answer blocks into smaller learnable chunks.',
            'Use the start time of the first included fragment and the end time of the last included fragment.'
        ],
        'output_shape': {
            'chunks': [
                {
                    'start': 'number in seconds',
                    'end': 'number in seconds',
                    'text': 'merged learning chunk text'
                }
            ]
        },
        'captions': compact_entries
    }

    data = {
        'model': OPENAI_CHUNKING_MODEL,
        'messages': [
            {
                'role': 'system',
                'content': 'You create natural, repeatable English-learning transcript chunks from timestamped captions.'
            },
            {
                'role': 'user',
                'content': json.dumps(prompt, ensure_ascii=False)
            }
        ],
        'temperature': 0.1,
        'response_format': {'type': 'json_object'},
        'max_tokens': 4000
    }

    try:
        req = urllib.request.Request(
            'https://api.openai.com/v1/chat/completions',
            data=json.dumps(data).encode('utf-8'),
            headers={
                'Content-Type': 'application/json',
                'Authorization': f'Bearer {OPENAI_API_KEY}'
            }
        )
        with urllib.request.urlopen(req, timeout=120) as response:
            result = json.loads(response.read().decode('utf-8'))
            content = result['choices'][0]['message']['content']
            parsed = json.loads(content)
    except Exception as e:
        print(f"AI chunking failed, using fallback grouping: {e}")
        return group_transcript_for_study(transcript_data)

    chunks = parsed.get('chunks', [])
    grouped = []
    for chunk in chunks:
        text = (chunk.get('text') or '').strip()
        if not text:
            continue

        try:
            start = float(chunk.get('start', 0))
            end = float(chunk.get('end', start))
        except (TypeError, ValueError):
            continue

        if end < start:
            continue

        grouped.append({
            'text': text,
            'start': start,
            'duration': max(0, end - start)
        })

    return grouped or group_transcript_for_study(transcript_data)

def extract_transcript_from_media_url(url, output_stem):
    """Download audio from a general media URL and transcribe it."""
    desktop_path = get_desktop_path()
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    audio_path = os.path.join(desktop_path, f"{output_stem}_source_audio_{timestamp}.mp3")

    audio_path = download_full_audio_file(url, audio_path)
    raw_transcript, transcript_data = transcribe_audio_with_openai(audio_path)

    if not raw_transcript:
        raise Exception("Audio was downloaded, but transcription returned no text")

    return raw_transcript, transcript_data, audio_path

def extract_audio_segment(video_url, start_time, end_time, output_filename):
    """Extract audio segment from YouTube video using yt-dlp and ffmpeg"""
    try:
        # Calculate duration
        duration = end_time - start_time

        # Use yt-dlp to download the specific segment
        cmd = [
            'yt-dlp',
            '-f', 'bestaudio/best',
            '--extract-audio',
            '--audio-format', 'mp3',
            '--audio-quality', '0',
            '--external-downloader', 'ffmpeg',
            '--external-downloader-args', f'-ss {start_time} -t {duration}',
            '-o', output_filename,
            video_url
        ]
        add_ytdlp_auth_args(cmd)

        run_media_command(cmd, 120, 'Audio segment extraction')

        return output_filename

    except subprocess.TimeoutExpired:
        raise Exception("Audio extraction timed out")
    except Exception as e:
        raise Exception(f"Failed to extract audio: {str(e)}")

@app.route('/extract', methods=['POST'])
def extract():
    """Extract and format transcript from YouTube captions or general media audio."""
    try:
        data = request.json
        if not data:
            return jsonify({'error': 'Invalid request data'}), 400

        url = data.get('url', '') or ''
        filename = data.get('filename', '') or ''

        if not url or not url.strip():
            return jsonify({'error': 'YouTube URL is required'}), 400

        url = url.strip()
        filename = filename.strip()

        source_type = 'media_transcription'
        video_id = None
        source_audio_file = None
        output_stem = safe_file_stem(filename, 'transcript')

        # Step 1: Prefer YouTube captions when possible. They are fast and already timestamped.
        if is_youtube_url(url):
            video_id = extract_video_id(url)
            if not video_id:
                return jsonify({'error': 'Invalid YouTube URL'}), 400

            try:
                raw_transcript, transcript_data = get_transcript(video_id)
                source_type = 'youtube_captions'
            except Exception as caption_error:
                print(f"Caption extraction failed, falling back to audio transcription: {caption_error}")
                raw_transcript, transcript_data, source_audio_file = extract_transcript_from_media_url(url, output_stem)
                source_type = 'audio_transcription'
        else:
            raw_transcript, transcript_data, source_audio_file = extract_transcript_from_media_url(url, output_stem)

        # Step 2: Format with OpenAI
        formatted_transcript = format_with_openai(raw_transcript)

        # Step 3: Create natural timestamped transcript rows for learning
        study_transcript_data = group_transcript_with_ai(transcript_data)
        timestamped_transcript = create_timestamped_transcript(study_transcript_data)

        # Step 4: Save to file
        desktop_path = get_desktop_path()

        # Determine output filename
        if filename:
            output_filename = filename if filename.endswith('.txt') else f"{filename}.txt"
        else:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            source_id = video_id or output_stem
            output_filename = f"transcript_{source_id}_{timestamp}.txt"

        output_path = os.path.join(desktop_path, output_filename)

        # Save formatted transcript
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(formatted_transcript)

        # Also save timestamped version
        timestamped_filename = output_path.replace('.txt', '_timestamps.json')
        with open(timestamped_filename, 'w', encoding='utf-8') as f:
            json.dump({
                'video_id': video_id,
                'video_url': url,
                'source_type': source_type,
                'source_audio_file': source_audio_file,
                'extracted_at': datetime.now().isoformat(),
                'transcript': timestamped_transcript
            }, f, indent=2, ensure_ascii=False)

        return jsonify({
            'success': True,
            'transcript': formatted_transcript,
            'timestamped_transcript': timestamped_transcript,
            'file_path': output_path,
            'timestamped_file': timestamped_filename,
            'video_id': video_id,
            'video_url': url,
            'source_type': source_type,
            'source_audio_file': source_audio_file
        })

    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/health', methods=['GET'])
def health():
    """Health check endpoint"""
    return jsonify({'status': 'healthy'})

@app.route('/', methods=['GET'])
def index():
    """Serve the local web interface."""
    return send_from_directory(os.path.dirname(os.path.abspath(__file__)), 'index.html')

@app.route('/preview-audio/<path:filename>', methods=['GET'])
def preview_audio_file(filename):
    """Serve a generated segment preview audio file."""
    return send_from_directory(PREVIEW_AUDIO_DIR, filename, mimetype='audio/mpeg')

@app.route('/preview-audio', methods=['POST'])
def preview_audio():
    """Extract and return a short playable audio preview for one transcript segment."""
    try:
        data = request.json
        if not data:
            return jsonify({'error': 'Invalid request data'}), 400

        video_url = (data.get('video_url') or '').strip()
        if not video_url:
            return jsonify({'error': 'Video URL is required'}), 400

        start_time = max(0, float(data.get('start_time', 0)) - 0.15)
        duration = float(data.get('duration', 0) or 0)
        if duration <= 0:
            duration = 4

        end_time = start_time + min(duration + 0.4, 30)
        os.makedirs(PREVIEW_AUDIO_DIR, exist_ok=True)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"preview_{timestamp}_{uuid.uuid4().hex[:8]}.mp3"
        output_path = os.path.join(PREVIEW_AUDIO_DIR, filename)

        extract_audio_segment(video_url, start_time, end_time, output_path)

        return jsonify({
            'success': True,
            'audio_url': f"/preview-audio/{filename}",
            'start_time': format_timestamp(start_time),
            'duration': end_time - start_time
        })

    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/extract-audio', methods=['POST'])
def extract_audio():
    """Extract audio segment from YouTube video for language learning"""
    try:
        data = request.json
        if not data:
            return jsonify({'error': 'Invalid request data'}), 400

        video_url = data.get('video_url', '') or ''
        if not video_url or not video_url.strip():
            return jsonify({'error': 'Video URL is required'}), 400

        video_url = video_url.strip()
        start_time = float(data.get('start_time', 0))
        end_time = float(data.get('end_time', 0))
        segment_name = (data.get('segment_name') or 'segment').strip()

        if start_time >= end_time:
            return jsonify({'error': 'End time must be greater than start time'}), 400

        # Create output filename
        desktop_path = get_desktop_path()
        safe_name = safe_file_stem(segment_name, 'segment')
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_filename = os.path.join(desktop_path, f"{safe_name}_{timestamp}.mp3")

        # Extract audio segment
        result_file = extract_audio_segment(video_url, start_time, end_time, output_filename)

        return jsonify({
            'success': True,
            'audio_file': result_file,
            'start_time': format_timestamp(start_time),
            'end_time': format_timestamp(end_time),
            'duration': end_time - start_time
        })

    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/extract-batch-audio', methods=['POST'])
def extract_batch_audio():
    """Extract multiple audio segments and combine them for language learning"""
    try:
        data = request.json
        if not data:
            return jsonify({'error': 'Invalid request data'}), 400

        video_url = data.get('video_url', '') or ''
        segments = data.get('segments', [])
        output_name = (data.get('output_name') or 'combined').strip()

        if not video_url or not video_url.strip():
            return jsonify({'error': 'Video URL is required'}), 400

        if not segments:
            return jsonify({'error': 'No segments provided'}), 400

        video_url = video_url.strip()
        desktop_path = get_desktop_path()
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

        # Extract each segment
        audio_files = []
        total_duration = 0

        for i, segment in enumerate(segments):
            start_time = float(segment['start'])
            end_time = start_time + float(segment['duration'])
            total_duration += float(segment['duration'])

            # Create output filename for this segment
            segment_filename = os.path.join(desktop_path, f"{output_name}_part{i+1}_{timestamp}.mp3")

            # Extract audio for this segment
            try:
                extract_audio_segment(video_url, start_time, end_time, segment_filename)
                audio_files.append(segment_filename)
            except Exception as e:
                print(f"Warning: Failed to extract segment {i+1}: {str(e)}")

        if not audio_files:
            return jsonify({'error': 'Failed to extract any audio segments'}), 500

        # Combine all audio files using ffmpeg
        combined_filename = os.path.join(desktop_path, f"{output_name}_combined_{timestamp}.mp3")

        # Create a list file for ffmpeg concat
        list_file = os.path.join(desktop_path, f"ffmpeg_list_{timestamp}.txt")
        with open(list_file, 'w') as f:
            for audio_file in audio_files:
                f.write(f"file '{audio_file}'\n")

        # Use ffmpeg to combine
        cmd = [
            'ffmpeg',
            '-f', 'concat',
            '-safe', '0',
            '-i', list_file,
            '-c', 'copy',
            combined_filename,
            '-y'  # Overwrite output file if exists
        ]

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)

        # Clean up the list file
        try:
            os.remove(list_file)
        except:
            pass

        if result.returncode != 0:
            # If combining failed, at least return individual files
            return jsonify({
                'success': True,
                'audio_files': audio_files,
                'combined_file': None,
                'total_duration': total_duration,
                'segment_count': len(audio_files)
            })

        # Clean up individual files after successful combine
        for audio_file in audio_files:
            try:
                os.remove(audio_file)
            except:
                pass

        return jsonify({
            'success': True,
            'audio_files': audio_files,
            'combined_file': combined_filename,
            'total_duration': total_duration,
            'segment_count': len(audio_files)
        })

    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/extract-batch-video', methods=['POST'])
def extract_batch_video():
    """Extract multiple video segments and combine them for language learning"""
    try:
        data = request.json
        if not data:
            return jsonify({'error': 'Invalid request data'}), 400

        video_url = data.get('video_url', '') or ''
        segments = data.get('segments', [])
        output_name = (data.get('output_name') or 'combined').strip()

        if not video_url or not video_url.strip():
            return jsonify({'error': 'Video URL is required'}), 400

        if not segments:
            return jsonify({'error': 'No segments provided'}), 400

        video_url = video_url.strip()
        desktop_path = get_desktop_path()
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

        # Extract each segment
        video_files = []
        total_duration = 0

        for i, segment in enumerate(segments):
            start_time = float(segment['start'])
            end_time = start_time + float(segment['duration'])
            total_duration += float(segment['duration'])

            # Create output filename for this segment
            segment_filename = os.path.join(desktop_path, f"{output_name}_part{i+1}_{timestamp}.mp4")

            # Extract video for this segment
            try:
                extract_video_segment(video_url, start_time, end_time, segment_filename)
                video_files.append(segment_filename)
            except Exception as e:
                print(f"Warning: Failed to extract segment {i+1}: {str(e)}")

        if not video_files:
            return jsonify({'error': 'Failed to extract any video segments'}), 500

        # Combine all video files using ffmpeg
        combined_filename = os.path.join(desktop_path, f"{output_name}_combined_{timestamp}.mp4")

        # Create a list file for ffmpeg concat
        list_file = os.path.join(desktop_path, f"ffmpeg_list_{timestamp}.txt")
        with open(list_file, 'w') as f:
            for video_file in video_files:
                f.write(f"file '{video_file}'\n")

        # Use ffmpeg to combine
        cmd = [
            'ffmpeg',
            '-f', 'concat',
            '-safe', '0',
            '-i', list_file,
            '-c', 'copy',
            combined_filename,
            '-y'  # Overwrite output file if exists
        ]

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)

        # Clean up the list file
        try:
            os.remove(list_file)
        except:
            pass

        if result.returncode != 0:
            # If combining failed, at least return individual files
            return jsonify({
                'success': True,
                'audio_files': video_files,
                'combined_file': None,
                'total_duration': total_duration,
                'segment_count': len(video_files)
            })

        # Clean up individual files after successful combine
        for video_file in video_files:
            try:
                os.remove(video_file)
            except:
                pass

        return jsonify({
            'success': True,
            'audio_files': video_files,
            'combined_file': combined_filename,
            'total_duration': total_duration,
            'segment_count': len(video_files)
        })

    except Exception as e:
        return jsonify({'error': str(e)}), 500

def extract_video_segment(video_url, start_time, end_time, output_filename):
    """Extract video segment from YouTube video using yt-dlp and ffmpeg"""
    try:
        # Calculate duration
        duration = end_time - start_time

        # Use yt-dlp to download the specific video segment
        cmd = [
            'yt-dlp',
            '-f', 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best',
            '--external-downloader', 'ffmpeg',
            '--external-downloader-args', f'-ss {start_time} -t {duration}',
            '-o', output_filename,
            video_url
        ]
        add_ytdlp_auth_args(cmd)

        run_media_command(cmd, 300, 'Video segment extraction')

        return output_filename

    except subprocess.TimeoutExpired:
        raise Exception("Video extraction timed out")
    except Exception as e:
        raise Exception(f"Failed to extract video: {str(e)}")

@app.route('/download-full-audio', methods=['POST'])
def download_full_audio():
    """Download full audio from YouTube video"""
    try:
        data = request.json
        if not data:
            return jsonify({'error': 'Invalid request data'}), 400

        video_url = data.get('video_url', '') or ''
        output_name = (data.get('output_name') or 'full_audio').strip()

        if not video_url or not video_url.strip():
            return jsonify({'error': 'Video URL is required'}), 400

        video_url = video_url.strip()
        desktop_path = get_desktop_path()
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_name = safe_file_stem(output_name, 'full_audio')
        output_filename = os.path.join(desktop_path, f"{safe_name}_{timestamp}.mp3")

        # Extract full audio
        output_filename = download_full_audio_file(video_url, output_filename)

        # Get file size
        file_size = os.path.getsize(output_filename)

        return jsonify({
            'success': True,
            'audio_file': output_filename,
            'file_size': file_size
        })

    except Exception as e:
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    port = int(os.getenv('PORT', '8002'))
    print("🚀 Starting YouTube Transcript Extractor Server...")
    print(f"📡 Server running at: http://localhost:{port}")
    print(f"📝 Open http://localhost:{port} to use the interface")
    app.run(host='localhost', port=port, debug=False)
