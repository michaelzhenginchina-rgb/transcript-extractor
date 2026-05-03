# 🎬 English Learning Transcript Extractor

**Turn video URLs into English-learning transcripts, audio, and practice clips**

A local web interface to extract YouTube captions, transcribe audio from general media URLs such as Instagram Reels, and format the result into readable English study material.

## ✨ Features

- 🔍 **YouTube Transcript Extraction** - Uses available YouTube captions when possible
- 🎧 **General Media Transcription** - Downloads audio from `yt-dlp` supported URLs and transcribes it with OpenAI
- 🤖 **AI-Powered Formatting** - Uses OpenAI to add proper punctuation and structure
- ⏱️ **Timestamped Study Mode** - Select transcript segments for focused practice
- ▶️ **Segment Audio Preview** - Play the audio for a single timestamped line
- ✎ **Quick Notes** - Highlight text in the plain transcript and mark it directly into notes
- 🎵 **Audio Clip Export** - Download selected study segments or full audio
- 🎥 **Video Clip Export** - Download selected video segments for review
- ✅ **Error Correction** - Automatically fixes common subtitle errors
- 💾 **Auto-Save** - Saves formatted transcripts to Desktop
- 🎨 **Beautiful UI** - Clean, minimalistic interface
- 🚀 **Quick Launch** - One-command startup

## 🚀 Quick Start

### Installation

1. Clone the repository:
```bash
git clone https://github.com/YOUR_USERNAME/transcript-extractor.git
cd transcript-extractor
```

2. Create virtual environment:
```bash
python3 -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
```

3. Install dependencies:
```bash
pip install -r requirements.txt
```

4. Install media tools:
```bash
brew install yt-dlp ffmpeg
```

5. Set up your OpenAI API key:
```bash
# Create .env file and add your API key
echo "OPENAI_API_KEY=your_key_here" > .env
```

### Usage

#### Option 1: Quick Launch (macOS)
```bash
bash start.sh
```

#### Option 2: Manual Start
```bash
# Start the server
python server.py

# Open index.html in your browser
open index.html  # macOS
# or double-click index.html
```

## 📖 How It Works

1. **Try captions first** - YouTube URLs use `youtube-transcript-api` when captions are available
2. **Fallback to audio** - Other URLs use `yt-dlp` to download audio and OpenAI transcription to create text
3. **Format** - Sends the raw transcript to OpenAI for intelligent formatting
4. **Study** - Uses AI to group tiny caption fragments into natural timestamped learning chunks
5. **Save** - Automatically saves formatted transcript and timestamp data to Desktop

## 🔧 Configuration

### Environment Variables (.env)
```env
OPENAI_API_KEY=your_openai_api_key_here
OPENAI_TRANSCRIPTION_MODEL=whisper-1
OPENAI_CHUNKING_MODEL=gpt-4o-mini
YTDLP_COOKIES_FROM_BROWSER=
YTDLP_COOKIES_FILE=
```

For Instagram links that require login, set either `YTDLP_COOKIES_FROM_BROWSER=chrome` or `YTDLP_COOKIES_FILE=/path/to/cookies.txt`.

### Server Port
Default: `localhost:8002` (configurable in `server.py`)

## 🛠️ Tech Stack

- **Frontend**: HTML5, CSS3, JavaScript
- **Backend**: Flask (Python)
- **AI**: OpenAI GPT-4o-mini + audio transcription
- **YouTube**: youtube-transcript-api
- **Media**: yt-dlp, ffmpeg

## 📝 API Endpoints

### POST /extract
Extract and format transcript from a YouTube URL or general media URL.

**Request:**
```json
{
  "url": "https://www.youtube.com/watch?v=VIDEO_ID",
  "filename": "optional-filename"
}
```

**Response:**
```json
{
  "success": true,
  "transcript": "Formatted transcript text...",
  "file_path": "/Users/username/Desktop/transcript.txt",
  "video_id": "VIDEO_ID"
}
```

### GET /health
Health check endpoint.

## 🤝 Contributing

Contributions are welcome! Please feel free to submit a Pull Request.

## 📄 License

This project is open source and available under the MIT License.

---

Made with ❤️
