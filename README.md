# 🎬 Transcript Extractor

**Automated YouTube video transcript extraction with AI-powered formatting**

A beautiful, minimalistic web interface to extract YouTube video transcripts and format them using OpenAI's GPT-4 for professional-quality output.

## ✨ Features

- 🔍 **Automatic Transcript Extraction** - Works with any YouTube video
- 🤖 **AI-Powered Formatting** - Uses GPT-4 to add proper punctuation and structure
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
pip install flask flask-cors youtube-transcript-api python-dotenv
```

4. Set up your OpenAI API key:
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

1. **Extract** - Uses `youtube-transcript-api` to fetch raw transcripts
2. **Format** - Sends to OpenAI GPT-4 for intelligent formatting
3. **Save** - Automatically saves formatted transcript to Desktop

## 🔧 Configuration

### Environment Variables (.env)
```env
OPENAI_API_KEY=your_openai_api_key_here
```

### Server Port
Default: `localhost:8002` (configurable in `server.py`)

## 🛠️ Tech Stack

- **Frontend**: HTML5, CSS3, JavaScript
- **Backend**: Flask (Python)
- **AI**: OpenAI GPT-4o-mini
- **YouTube**: youtube-transcript-api

## 📝 API Endpoints

### POST /extract
Extract and format transcript from YouTube URL.

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
