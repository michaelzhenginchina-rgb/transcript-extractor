#!/usr/bin/env python3
"""
YouTube Transcript Extractor Server
Runs on localhost:8002
"""

from flask import Flask, request, jsonify
from flask_cors import CORS
import sys
import os
import re
import json
import urllib.request
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
    """Extract video ID from YouTube URL"""
    patterns = [
        r'(?:youtube\.com\/watch\?v=|youtu\.be\/|youtube\.com\/embed\/)([a-zA-Z0-9_-]{11})',
        r'youtube\.com\/watch\?.*v=([a-zA-Z0-9_-]{11})'
    ]
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1)
    return None

def get_transcript(video_id):
    """Extract transcript using youtube-transcript-api"""
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

        # Fetch the transcript
        transcript_data = transcript_obj.fetch()

        # Extract text
        raw_text = '\n'.join([entry.text for entry in transcript_data])
        return raw_text

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

@app.route('/extract', methods=['POST'])
def extract():
    """Extract and format transcript from YouTube URL"""
    try:
        data = request.json
        url = data.get('url', '').strip()
        filename = data.get('filename', '').strip()

        if not url:
            return jsonify({'error': 'YouTube URL is required'}), 400

        # Extract video ID
        video_id = extract_video_id(url)
        if not video_id:
            return jsonify({'error': 'Invalid YouTube URL'}), 400

        # Step 1: Extract raw transcript
        raw_transcript = get_transcript(video_id)

        # Step 2: Format with OpenAI
        formatted_transcript = format_with_openai(raw_transcript)

        # Step 3: Save to file
        desktop_path = os.path.expanduser("~/Desktop")

        # Determine output filename
        if filename:
            output_filename = filename if filename.endswith('.txt') else f"{filename}.txt"
        else:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            output_filename = f"transcript_{video_id}_{timestamp}.txt"

        output_path = os.path.join(desktop_path, output_filename)

        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(formatted_transcript)

        return jsonify({
            'success': True,
            'transcript': formatted_transcript,
            'file_path': output_path,
            'video_id': video_id
        })

    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/health', methods=['GET'])
def health():
    """Health check endpoint"""
    return jsonify({'status': 'healthy'})

if __name__ == '__main__':
    print("🚀 Starting YouTube Transcript Extractor Server...")
    print("📡 Server running at: http://localhost:8002")
    print("📝 Open index.html to use the interface")
    app.run(host='localhost', port=8002, debug=False)
