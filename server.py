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
import urllib.parse
import subprocess
import tempfile
import shutil
import zipfile
from collections import Counter
from datetime import datetime
from dotenv import load_dotenv
from werkzeug.utils import secure_filename

# Load environment variables from .env file
load_dotenv()

# Add parent directory to path to import the formatter
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

app = Flask(__name__)
CORS(app)

OPENAI_API_KEY = os.getenv('OPENAI_API_KEY')
WHISPER_MODEL = os.getenv('WHISPER_MODEL', os.path.expanduser('~/.cache/whisper-cpp/ggml-small.bin'))
WHISPER_CLI = os.getenv('WHISPER_CLI') or shutil.which('whisper-cli') or '/opt/homebrew/bin/whisper-cli'
FFMPEG = os.getenv('FFMPEG') or shutil.which('ffmpeg') or '/opt/homebrew/bin/ffmpeg'
MEDIA_EXTENSIONS = {'.mp3', '.wav', '.flac', '.ogg', '.mp4', '.mov', '.m4a', '.aac', '.webm'}
DIRECT_AUDIO_EXTENSIONS = {'.mp3', '.wav', '.flac', '.ogg'}

def is_youtube_url(url):
    """Return True when the submitted URL is a YouTube link."""
    parsed = urllib.parse.urlparse(url)
    hostname = (parsed.hostname or '').lower()
    return hostname in {'youtube.com', 'www.youtube.com', 'm.youtube.com', 'youtu.be'} or hostname.endswith('.youtube.com')

def safe_slug(value, fallback='media'):
    """Create a filesystem-safe short slug."""
    slug = re.sub(r'[^\w\s-]', '', value or '').strip().replace(' ', '_')
    return slug[:80] or fallback

def extract_video_id(url):
    """Extract video ID from supported YouTube URL shapes."""
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

    return None

def make_plain_timestamped_transcript(transcript_text):
    """Create simple transcript segments when Whisper does not provide timestamps."""
    chunks = [chunk.strip() for chunk in re.split(r'\n{2,}', transcript_text or '') if chunk.strip()]
    if not chunks and transcript_text.strip():
        chunks = [transcript_text.strip()]
    return [
        {
            'timestamp': '00:00',
            'start': 0,
            'duration': 0,
            'text': chunk
        }
        for chunk in chunks
    ]

def download_url_audio(url, output_dir, output_stem):
    """Download audio from a supported public URL with yt-dlp."""
    os.makedirs(output_dir, exist_ok=True)
    output_template = os.path.join(output_dir, f"{output_stem}.%(ext)s")
    base_cmd = [
        'yt-dlp',
        '-f', 'bestaudio/best',
        '--extract-audio',
        '--audio-format', 'mp3',
        '--audio-quality', '0',
        '--no-playlist',
        '-o', output_template,
    ]

    attempts = [
        base_cmd + [url],
        base_cmd + ['--cookies-from-browser', 'chrome', url],
    ]

    errors = []
    for cmd in attempts:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        if result.returncode == 0:
            break
        errors.append(result.stderr[-1600:])
    else:
        raise Exception("yt-dlp could not download audio:\n" + "\n\n".join(errors))

    candidates = [
        os.path.join(output_dir, name)
        for name in os.listdir(output_dir)
        if name.startswith(output_stem + '.') and os.path.splitext(name)[1].lower() in DIRECT_AUDIO_EXTENSIONS
    ]
    if not candidates:
        raise Exception("yt-dlp finished, but no audio file was created.")
    return max(candidates, key=os.path.getmtime)

def download_full_audio_with_ytdlp(url, output_filename):
    """Download a full MP3 with yt-dlp, retrying with Chrome cookies when needed."""
    base_cmd = [
        'yt-dlp',
        '-x',
        '--audio-format', 'mp3',
        '--audio-quality', '0',
        '-o', output_filename,
    ]
    attempts = [
        base_cmd + [url],
        base_cmd + ['--cookies-from-browser', 'chrome', url],
    ]

    errors = []
    for cmd in attempts:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        if result.returncode == 0:
            return
        errors.append(result.stderr[-1600:])

    raise Exception("Failed to download audio:\n" + "\n\n".join(errors))

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

def clean_transcript_text(text):
    """Remove timestamp noise and make Whisper output more readable."""
    text = re.sub(r'\[[^\]]+\]', ' ', text)
    text = re.sub(r'\s+', ' ', text).strip()
    if not text:
        return ''

    sentences = re.split(r'(?<=[.!?])\s+', text)
    return '\n\n'.join(sentence.strip() for sentence in sentences if sentence.strip())

def fallback_keywords(transcript_text):
    """Simple local keyword fallback if AI enrichment is unavailable."""
    stopwords = {
        'about', 'after', 'again', 'also', 'because', 'before', 'being', 'could',
        'first', 'from', 'have', 'into', 'just', 'like', 'more', 'most', 'only',
        'other', 'really', 'should', 'some', 'that', 'their', 'there', 'these',
        'they', 'this', 'those', 'through', 'very', 'want', 'well', 'were',
        'what', 'when', 'where', 'which', 'with', 'would', 'your', 'you', 'the',
        'and', 'for', 'but', 'are', 'was', 'can', 'did', 'how', 'who', 'why',
        'yes', 'not', 'all', 'had', 'has', 'his', 'her'
    }
    words = re.findall(r"[A-Za-z][A-Za-z'-]{3,}", transcript_text.lower())
    counts = Counter(word for word in words if word not in stopwords)
    return [
        {
            'term': word,
            'meaning': '中文释义待补充',
            'note': 'Useful word from the transcript'
        }
        for word, _ in counts.most_common(18)
    ]

def prepare_uploaded_audio(input_path, work_dir):
    """Return an audio file path Whisper can process. Converts video to wav."""
    ext = os.path.splitext(input_path)[1].lower()
    if ext in DIRECT_AUDIO_EXTENSIONS:
        return input_path
    if ext not in MEDIA_EXTENSIONS:
        raise Exception(f"Unsupported file type: {ext or 'unknown'}")

    wav_path = os.path.join(work_dir, 'audio.wav')
    cmd = [
        FFMPEG,
        '-y',
        '-i', input_path,
        '-vn',
        '-acodec', 'pcm_s16le',
        '-ar', '16000',
        '-ac', '1',
        wav_path
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise Exception(f"Could not extract audio with ffmpeg: {result.stderr[-1200:]}")
    return wav_path

def whisper_timestamp_to_seconds(value):
    """Convert whisper.cpp timestamps like HH:MM:SS,mmm to seconds."""
    match = re.match(r'(\d+):(\d+):(\d+)[,.](\d+)', value or '')
    if not match:
        return 0
    hours, minutes, seconds, millis = match.groups()
    return int(hours) * 3600 + int(minutes) * 60 + int(seconds) + int(millis[:3].ljust(3, '0')) / 1000

def read_whisper_segments(output_base):
    """Read timestamped segments from whisper.cpp JSON output."""
    json_path = f"{output_base}.json"
    if not os.path.exists(json_path):
        return []

    with open(json_path, 'r', encoding='utf-8', errors='replace') as f:
        data = json.load(f)

    segments = []
    for item in data.get('transcription', []):
        timestamps = item.get('timestamps') or {}
        start = whisper_timestamp_to_seconds(timestamps.get('from'))
        end = whisper_timestamp_to_seconds(timestamps.get('to'))
        text = (item.get('text') or '').strip()
        if not text:
            continue
        segments.append({
            'timestamp': format_timestamp(start),
            'start': start,
            'duration': max(0, end - start),
            'text': text
        })
    return segments

def transcribe_uploaded_media_with_segments(input_path, output_base):
    """Transcribe uploaded audio/video using local whisper-cli and return text + segments."""
    if not os.path.exists(WHISPER_MODEL):
        raise Exception(f"Whisper model not found: {WHISPER_MODEL}")

    with tempfile.TemporaryDirectory() as work_dir:
        audio_path = prepare_uploaded_audio(input_path, work_dir)
        cmd = [
            WHISPER_CLI,
            '-m', WHISPER_MODEL,
            '-f', audio_path,
            '-l', 'en',
            '-otxt',
            '-oj',
            '-of', output_base,
            '-nt',
            '--no-gpu'
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise Exception(f"Whisper transcription failed: {result.stderr[-1600:]}")

    txt_path = f"{output_base}.txt"
    if not os.path.exists(txt_path):
        raise Exception("Whisper finished, but no transcript text file was created.")

    with open(txt_path, 'r', encoding='utf-8', errors='replace') as f:
        transcript_text = clean_transcript_text(f.read())

    return transcript_text, read_whisper_segments(output_base)

def transcribe_uploaded_media(input_path, output_base):
    """Transcribe uploaded audio/video using local whisper-cli."""
    transcript_text, _segments = transcribe_uploaded_media_with_segments(input_path, output_base)
    return transcript_text

def create_learning_material_with_openai(transcript_text, title):
    """Create Chinese translation, keywords, sentence patterns, and notes."""
    if not OPENAI_API_KEY:
        return {
            'ai_available': False,
            'chinese_translation': '未配置 OpenAI API Key，因此暂时只生成英文 transcript。',
            'keywords': fallback_keywords(transcript_text),
            'sentence_patterns': [],
            'student_notes': [
                '先不看原文听一遍，记录你能听懂的信息。',
                '第二遍重点记录人物、地点、时间、原因和感受。',
                '最后对照 transcript 检查遗漏信息。'
            ]
        }

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
                "content": "You create concise English-Chinese learning materials for Chinese IELTS/English students. Return valid JSON only."
            },
            {
                "role": "user",
                "content": f"""Create learning material from this English transcript.

Return JSON only with this structure:
{{
  "chinese_translation": "complete natural Chinese translation",
  "keywords": [
    {{"term": "English word or phrase", "meaning": "Chinese meaning", "note": "short learning note"}}
  ],
  "sentence_patterns": ["useful sentence pattern"],
  "student_notes": ["short practical note in Chinese or simple English"]
}}

Title: {title}

Transcript:
{transcript_text}"""
            }
        ],
        "temperature": 0.2,
        "max_tokens": 4000
    }

    try:
        req = urllib.request.Request(url, data=json.dumps(data).encode('utf-8'), headers=headers)
        with urllib.request.urlopen(req, timeout=180) as response:
            result = json.loads(response.read().decode('utf-8'))
            content = result['choices'][0]['message']['content'].strip()
            content = re.sub(r'^```json\s*|\s*```$', '', content, flags=re.I | re.S).strip()
            learning = json.loads(content)
            learning['ai_available'] = True
            return learning
    except Exception as e:
        return {
            'ai_available': False,
            'chinese_translation': f'AI 生成失败：{str(e)}',
            'keywords': fallback_keywords(transcript_text),
            'sentence_patterns': [],
            'student_notes': ['Transcript 已生成，但中文翻译和关键词需要重新生成或手动检查。']
        }

def render_learning_markdown(title, transcript_text, learning):
    """Save a readable Markdown study file."""
    lines = [
        f"# {title}",
        "",
        "## English Transcript",
        "",
        transcript_text,
        "",
        "## Chinese Translation",
        "",
        learning.get('chinese_translation', ''),
        "",
        "## Keywords and Useful Phrases",
        ""
    ]
    for item in learning.get('keywords', []):
        term = item.get('term', '')
        meaning = item.get('meaning', '')
        note = item.get('note', '')
        note_text = f" ({note})" if note else ""
        lines.append(f"- **{term}** - {meaning}{note_text}")

    patterns = learning.get('sentence_patterns', [])
    if patterns:
        lines.extend(["", "## Useful Sentence Patterns", ""])
        lines.extend([f"- {pattern}" for pattern in patterns])

    notes = learning.get('student_notes', [])
    if notes:
        lines.extend(["", "## Student Notes", ""])
        lines.extend([f"{index}. {note}" for index, note in enumerate(notes, 1)])

    return "\n".join(lines).strip() + "\n"

def xml_escape(value):
    """Escape text for Word XML."""
    return (
        str(value or '')
        .replace('&', '&amp;')
        .replace('<', '&lt;')
        .replace('>', '&gt;')
        .replace('"', '&quot;')
    )

def docx_paragraph(text='', style=None, bold_prefix=None):
    """Create a simple Word paragraph XML string."""
    style_xml = f'<w:pStyle w:val="{style}"/>' if style else ''
    ppr = f'<w:pPr>{style_xml}</w:pPr>' if style_xml else ''

    if bold_prefix and str(text).startswith(bold_prefix):
        rest = str(text)[len(bold_prefix):]
        runs = (
            f'<w:r><w:rPr><w:b/></w:rPr><w:t xml:space="preserve">{xml_escape(bold_prefix)}</w:t></w:r>'
            f'<w:r><w:t xml:space="preserve">{xml_escape(rest)}</w:t></w:r>'
        )
    else:
        runs = f'<w:r><w:t xml:space="preserve">{xml_escape(text)}</w:t></w:r>' if text else ''

    return f'<w:p>{ppr}{runs}</w:p>'

def docx_table(rows):
    """Create a simple full-width two-column Word table."""
    row_xml = []
    for left, right in rows:
        row_xml.append(
            '<w:tr>'
            '<w:tc><w:tcPr><w:tcW w:w="3000" w:type="dxa"/></w:tcPr>'
            f'{docx_paragraph(left)}</w:tc>'
            '<w:tc><w:tcPr><w:tcW w:w="6360" w:type="dxa"/></w:tcPr>'
            f'{docx_paragraph(right)}</w:tc>'
            '</w:tr>'
        )
    return (
        '<w:tbl>'
        '<w:tblPr><w:tblW w:w="9360" w:type="dxa"/>'
        '<w:tblBorders>'
        '<w:top w:val="single" w:sz="4" w:space="0" w:color="D9E2EC"/>'
        '<w:left w:val="single" w:sz="4" w:space="0" w:color="D9E2EC"/>'
        '<w:bottom w:val="single" w:sz="4" w:space="0" w:color="D9E2EC"/>'
        '<w:right w:val="single" w:sz="4" w:space="0" w:color="D9E2EC"/>'
        '<w:insideH w:val="single" w:sz="4" w:space="0" w:color="D9E2EC"/>'
        '<w:insideV w:val="single" w:sz="4" w:space="0" w:color="D9E2EC"/>'
        '</w:tblBorders></w:tblPr>'
        '<w:tblGrid><w:gridCol w:w="3000"/><w:gridCol w:w="6360"/></w:tblGrid>'
        + ''.join(row_xml)
        + '</w:tbl>'
    )

def create_learning_docx(title, transcript_text, learning, docx_path):
    """Create a Word document containing the generated learning material."""
    body_parts = [
        docx_paragraph(title, 'Title'),
        docx_paragraph('English Transcript', 'Heading1'),
    ]

    for paragraph in transcript_text.split('\n\n'):
        body_parts.append(docx_paragraph(paragraph.strip()))

    body_parts.append(docx_paragraph('Chinese Translation', 'Heading1'))
    for paragraph in str(learning.get('chinese_translation', '')).split('\n'):
        if paragraph.strip():
            body_parts.append(docx_paragraph(paragraph.strip()))

    body_parts.append(docx_paragraph('Keywords and Useful Phrases', 'Heading1'))
    keyword_rows = [('English', 'Chinese / Notes')]
    for item in learning.get('keywords', []):
        term = item.get('term', '')
        meaning = item.get('meaning', '')
        note = item.get('note', '')
        right = meaning + (f'\n{note}' if note else '')
        keyword_rows.append((term, right))
    body_parts.append(docx_table(keyword_rows))

    patterns = learning.get('sentence_patterns', [])
    if patterns:
        body_parts.append(docx_paragraph('Useful Sentence Patterns', 'Heading1'))
        for pattern in patterns:
            body_parts.append(docx_paragraph(pattern, 'ListParagraph'))

    notes = learning.get('student_notes', [])
    if notes:
        body_parts.append(docx_paragraph('Student Notes', 'Heading1'))
        for index, note in enumerate(notes, 1):
            body_parts.append(docx_paragraph(f'{index}. {note}'))

    document_xml = f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body>
    {''.join(body_parts)}
    <w:sectPr>
      <w:pgSz w:w="12240" w:h="15840"/>
      <w:pgMar w:top="1440" w:right="1440" w:bottom="1440" w:left="1440" w:header="720" w:footer="720" w:gutter="0"/>
    </w:sectPr>
  </w:body>
</w:document>'''

    styles_xml = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:style w:type="paragraph" w:default="1" w:styleId="Normal">
    <w:name w:val="Normal"/>
    <w:rPr><w:rFonts w:ascii="Arial" w:hAnsi="Arial" w:eastAsia="Microsoft YaHei"/><w:sz w:val="24"/></w:rPr>
    <w:pPr><w:spacing w:after="120" w:line="276" w:lineRule="auto"/></w:pPr>
  </w:style>
  <w:style w:type="paragraph" w:styleId="Title">
    <w:name w:val="Title"/>
    <w:rPr><w:b/><w:rFonts w:ascii="Arial" w:hAnsi="Arial" w:eastAsia="Microsoft YaHei"/><w:sz w:val="40"/></w:rPr>
    <w:pPr><w:spacing w:after="240"/></w:pPr>
  </w:style>
  <w:style w:type="paragraph" w:styleId="Heading1">
    <w:name w:val="heading 1"/>
    <w:basedOn w:val="Normal"/>
    <w:next w:val="Normal"/>
    <w:rPr><w:b/><w:rFonts w:ascii="Arial" w:hAnsi="Arial" w:eastAsia="Microsoft YaHei"/><w:sz w:val="32"/></w:rPr>
    <w:pPr><w:spacing w:before="240" w:after="120"/></w:pPr>
  </w:style>
  <w:style w:type="paragraph" w:styleId="ListParagraph">
    <w:name w:val="List Paragraph"/>
    <w:basedOn w:val="Normal"/>
    <w:pPr><w:ind w:left="720"/></w:pPr>
  </w:style>
</w:styles>'''

    content_types = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
  <Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>
</Types>'''

    root_rels = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
</Relationships>'''

    doc_rels = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"/>'''

    with zipfile.ZipFile(docx_path, 'w', zipfile.ZIP_DEFLATED) as docx:
        docx.writestr('[Content_Types].xml', content_types)
        docx.writestr('_rels/.rels', root_rels)
        docx.writestr('word/document.xml', document_xml)
        docx.writestr('word/styles.xml', styles_xml)
        docx.writestr('word/_rels/document.xml.rels', doc_rels)

def generated_file_url(path):
    """Return a local download URL for generated study files."""
    return f"/download-generated?path={urllib.parse.quote(path)}"

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
    """Extract transcript from YouTube captions or by downloading audio and using Whisper."""
    try:
        data = request.json
        if not data:
            return jsonify({'error': 'Invalid request data'}), 400

        url = data.get('url', '') or ''
        filename = data.get('filename', '') or ''

        if not url or not url.strip():
            return jsonify({'error': 'URL is required'}), 400

        url = url.strip()
        filename = filename.strip()

        desktop_path = os.path.expanduser("~/Desktop")
        media_output_dir = os.path.join(desktop_path, "字幕提取-下载音频")
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        source_id = None
        source_type = 'url_audio_whisper'
        audio_file = None

        if is_youtube_url(url):
            video_id = extract_video_id(url)
            if not video_id:
                return jsonify({'error': 'Invalid YouTube URL'}), 400

            try:
                raw_transcript, transcript_data = get_transcript(video_id)
                formatted_transcript = format_with_openai(raw_transcript)
                timestamped_transcript = []
                for entry in transcript_data:
                    timestamp_label = format_timestamp(entry['start'])
                    timestamped_transcript.append({
                        'timestamp': timestamp_label,
                        'start': entry['start'],
                        'duration': entry.get('duration', 0),
                        'text': entry['text']
                    })
                source_id = video_id
                source_type = 'youtube_transcript'
            except Exception:
                source_id = video_id
                output_stem = safe_slug(filename or f"youtube_{video_id}_{timestamp}", "youtube_audio")
                audio_file = download_url_audio(url, media_output_dir, output_stem)
                output_base = os.path.join(media_output_dir, f"{output_stem}_raw")
                raw_transcript, whisper_segments = transcribe_uploaded_media_with_segments(audio_file, output_base)
                formatted_transcript = format_with_openai(raw_transcript)
                timestamped_transcript = whisper_segments or make_plain_timestamped_transcript(formatted_transcript)
                source_type = 'youtube_audio_whisper'
        else:
            parsed = urllib.parse.urlparse(url)
            host_slug = safe_slug(parsed.netloc.replace('.', '_'), 'remote')
            output_stem = safe_slug(filename or f"{host_slug}_{timestamp}", "remote_audio")
            audio_file = download_url_audio(url, media_output_dir, output_stem)
            output_base = os.path.join(media_output_dir, f"{output_stem}_raw")
            raw_transcript, whisper_segments = transcribe_uploaded_media_with_segments(audio_file, output_base)
            formatted_transcript = format_with_openai(raw_transcript)
            timestamped_transcript = whisper_segments or make_plain_timestamped_transcript(formatted_transcript)
            source_id = host_slug

        # Determine output filename
        if filename:
            output_filename = filename if filename.endswith('.txt') else f"{filename}.txt"
        else:
            output_filename = f"transcript_{source_id}_{timestamp}.txt"

        output_path = os.path.join(desktop_path, output_filename)

        # Save formatted transcript
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(formatted_transcript)

        # Also save timestamped version
        timestamped_filename = output_path.replace('.txt', '_timestamps.json')
        with open(timestamped_filename, 'w', encoding='utf-8') as f:
            json.dump({
                'video_id': source_id,
                'video_url': url,
                'source_type': source_type,
                'audio_file': audio_file,
                'extracted_at': datetime.now().isoformat(),
                'transcript': timestamped_transcript
            }, f, indent=2, ensure_ascii=False)

        return jsonify({
            'success': True,
            'transcript': formatted_transcript,
            'timestamped_transcript': timestamped_transcript,
            'file_path': output_path,
            'timestamped_file': timestamped_filename,
            'video_id': source_id,
            'video_url': url,
            'source_type': source_type,
            'audio_file': audio_file
        })

    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/health', methods=['GET'])
def health():
    """Health check endpoint"""
    return jsonify({'status': 'healthy'})

@app.route('/download-generated', methods=['GET'])
def download_generated():
    """Download generated study files from the local output folder."""
    try:
        path = request.args.get('path', '')
        if not path:
            return jsonify({'error': 'File path is required'}), 400

        output_dir = os.path.realpath(os.path.join(os.path.expanduser("~/Desktop"), "字幕提取-学习材料"))
        requested_path = os.path.realpath(path)

        if not requested_path.startswith(output_dir + os.sep):
            return jsonify({'error': 'This file is outside the generated learning-material folder'}), 403
        if not os.path.exists(requested_path):
            return jsonify({'error': 'File not found'}), 404

        return send_file(requested_path, as_attachment=True)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/generate-learning-material', methods=['POST'])
def generate_learning_material():
    """Upload local audio/video and generate transcript + learning notes."""
    try:
        if 'media' not in request.files:
            return jsonify({'error': 'Please upload an audio or video file'}), 400

        media_file = request.files['media']
        if not media_file or not media_file.filename:
            return jsonify({'error': 'Please choose a file'}), 400

        original_filename = secure_filename(media_file.filename) or 'uploaded_media'
        ext = os.path.splitext(original_filename)[1].lower()
        if ext not in MEDIA_EXTENSIONS:
            return jsonify({'error': f'Unsupported file type: {ext}. Please use mp3, mp4, wav, mov, m4a, flac, ogg, aac, or webm.'}), 400

        title = (request.form.get('title') or '').strip()
        if not title:
            title = os.path.splitext(original_filename)[0].replace('_', ' ').replace('-', ' ').strip() or 'Learning Material'

        desktop_path = os.path.expanduser("~/Desktop")
        output_dir = os.path.join(desktop_path, "字幕提取-学习材料")
        upload_dir = os.path.join(output_dir, "uploads")
        os.makedirs(upload_dir, exist_ok=True)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_title = re.sub(r'[^\w\s-]', '', title).strip().replace(' ', '_') or 'learning_material'
        saved_media_path = os.path.join(upload_dir, f"{safe_title}_{timestamp}{ext}")
        media_file.save(saved_media_path)

        output_base = os.path.join(output_dir, f"{safe_title}_{timestamp}_raw")
        transcript_text = transcribe_uploaded_media(saved_media_path, output_base)
        learning = create_learning_material_with_openai(transcript_text, title)

        markdown = render_learning_markdown(title, transcript_text, learning)
        markdown_path = os.path.join(output_dir, f"{safe_title}_{timestamp}_study_notes.md")
        json_path = os.path.join(output_dir, f"{safe_title}_{timestamp}_study_notes.json")
        docx_path = os.path.join(output_dir, f"{safe_title}_{timestamp}_study_notes.docx")

        with open(markdown_path, 'w', encoding='utf-8') as f:
            f.write(markdown)
        with open(json_path, 'w', encoding='utf-8') as f:
            json.dump({
                'title': title,
                'source_file': saved_media_path,
                'transcript': transcript_text,
                'learning': learning,
                'created_at': datetime.now().isoformat()
            }, f, indent=2, ensure_ascii=False)
        create_learning_docx(title, transcript_text, learning, docx_path)

        return jsonify({
            'success': True,
            'title': title,
            'transcript': transcript_text,
            'chinese_translation': learning.get('chinese_translation', ''),
            'keywords': learning.get('keywords', []),
            'sentence_patterns': learning.get('sentence_patterns', []),
            'student_notes': learning.get('student_notes', []),
            'ai_available': learning.get('ai_available', False),
            'source_file': saved_media_path,
            'markdown_file': markdown_path,
            'json_file': json_path,
            'docx_file': docx_path,
            'docx_download_url': generated_file_url(docx_path)
        })

    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/generate-learning-from-transcript', methods=['POST'])
def generate_learning_from_transcript():
    """Generate learning notes from an already extracted YouTube transcript."""
    try:
        data = request.json
        if not data:
            return jsonify({'error': 'Invalid request data'}), 400

        transcript_text = (data.get('transcript') or '').strip()
        title = (data.get('title') or data.get('filename') or 'YouTube Learning Material').strip()

        if not transcript_text:
            return jsonify({'error': 'Transcript is required'}), 400

        desktop_path = os.path.expanduser("~/Desktop")
        output_dir = os.path.join(desktop_path, "字幕提取-学习材料")
        os.makedirs(output_dir, exist_ok=True)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_title = re.sub(r'[^\w\s-]', '', title).strip().replace(' ', '_') or 'youtube_learning_material'

        learning = create_learning_material_with_openai(transcript_text, title)
        markdown = render_learning_markdown(title, transcript_text, learning)

        markdown_path = os.path.join(output_dir, f"{safe_title}_{timestamp}_study_notes.md")
        json_path = os.path.join(output_dir, f"{safe_title}_{timestamp}_study_notes.json")
        docx_path = os.path.join(output_dir, f"{safe_title}_{timestamp}_study_notes.docx")

        with open(markdown_path, 'w', encoding='utf-8') as f:
            f.write(markdown)
        with open(json_path, 'w', encoding='utf-8') as f:
            json.dump({
                'title': title,
                'source': 'youtube_transcript',
                'transcript': transcript_text,
                'learning': learning,
                'created_at': datetime.now().isoformat()
            }, f, indent=2, ensure_ascii=False)
        create_learning_docx(title, transcript_text, learning, docx_path)

        return jsonify({
            'success': True,
            'title': title,
            'transcript': transcript_text,
            'chinese_translation': learning.get('chinese_translation', ''),
            'keywords': learning.get('keywords', []),
            'sentence_patterns': learning.get('sentence_patterns', []),
            'student_notes': learning.get('student_notes', []),
            'ai_available': learning.get('ai_available', False),
            'markdown_file': markdown_path,
            'json_file': json_path,
            'docx_file': docx_path,
            'docx_download_url': generated_file_url(docx_path)
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
        desktop_path = os.path.expanduser("~/Desktop")
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_filename = os.path.join(desktop_path, f"{output_name}_{timestamp}.mp3")

        download_full_audio_with_ytdlp(video_url, output_filename)

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
    print("🚀 Starting YouTube Transcript Extractor Server...")
    print("📡 Server running at: http://localhost:8002")
    print("📝 Open index.html to use the interface")
    app.run(host='localhost', port=8002, debug=False)
