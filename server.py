#!/usr/bin/env python3
"""
YouTube Transcript Extractor Server
Runs on localhost:8002
"""

from flask import Flask, request, jsonify, send_file
from flask_cors import CORS
import sys
import os
import re
import json
import urllib.request
import subprocess
from datetime import datetime
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Add parent directory to path to import the formatter
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

app = Flask(__name__)
CORS(app)

OPENAI_API_KEY = os.getenv('OPENAI_API_KEY')

def extract_video_id(url):
    """Extract video ID from YouTube URL (including short URLs)"""
    patterns = [
        # Standard YouTube watch URLs
        r'youtube\.com\/watch\?.*v=([a-zA-Z0-9_-]{11})',
        # Short youtu.be URLs
        r'youtu\.be\/([a-zA-Z0-9_-]{11})',
        # Embed URLs
        r'youtube\.com\/embed\/([a-zA-Z0-9_-]{11})',
        # Short URLs with parameters
        r'youtu\.be\/([a-zA-Z0-9_-]{11})\?',
    ]
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1)

    # If no match, try to extract any 11-character YouTube ID pattern
    generic_match = re.search(r'([a-zA-Z0-9_-]{11})', url)
    if generic_match:
        return generic_match.group(1)

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

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)

        if result.returncode != 0:
            raise Exception(f"Failed to extract audio: {result.stderr}")

        return output_filename

    except subprocess.TimeoutExpired:
        raise Exception("Audio extraction timed out")
    except Exception as e:
        raise Exception(f"Failed to extract audio: {str(e)}")

@app.route('/extract', methods=['POST'])
def extract():
    """Extract and format transcript from YouTube URL with timestamps"""
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

        # Extract video ID
        video_id = extract_video_id(url)
        if not video_id:
            return jsonify({'error': 'Invalid YouTube URL'}), 400

        # Step 1: Extract raw transcript with timestamps
        raw_transcript, transcript_data = get_transcript(video_id)

        # Step 2: Format with OpenAI
        formatted_transcript = format_with_openai(raw_transcript)

        # Step 3: Create timestamped transcript (for learning)
        timestamped_transcript = []
        for entry in transcript_data:
            timestamp = format_timestamp(entry['start'])
            timestamped_transcript.append({
                'timestamp': timestamp,
                'start': entry['start'],
                'duration': entry.get('duration', 0),
                'text': entry['text']
            })

        # Step 4: Save to file
        desktop_path = os.path.expanduser("~/Desktop")

        # Determine output filename
        if filename:
            output_filename = filename if filename.endswith('.txt') else f"{filename}.txt"
        else:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            output_filename = f"transcript_{video_id}_{timestamp}.txt"

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
            'video_url': url
        })

    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/health', methods=['GET'])
def health():
    """Health check endpoint"""
    return jsonify({'status': 'healthy'})

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
        desktop_path = os.path.expanduser("~/Desktop")
        safe_name = re.sub(r'[^\w\s-]', '', segment_name).strip().replace(' ', '_')
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
        desktop_path = os.path.expanduser("~/Desktop")
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
        desktop_path = os.path.expanduser("~/Desktop")
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

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)

        if result.returncode != 0:
            raise Exception(f"Failed to extract video: {result.stderr}")

        return output_filename

    except subprocess.TimeoutExpired:
        raise Exception("Video extraction timed out")
    except Exception as e:
        raise Exception(f"Failed to extract video: {str(e)}")

if __name__ == '__main__':
    print("🚀 Starting YouTube Transcript Extractor Server...")
    print("📡 Server running at: http://localhost:8002")
    print("📝 Open index.html to use the interface")
    app.run(host='localhost', port=8002, debug=False)
