# RAGChat — PDF Intelligence Chatbot

A full-stack RAG (Retrieval-Augmented Generation) chatbot that lets users upload PDFs and ask questions about them. Built with Flask + Vanilla JS, powered by Claude AI.

---

## Features

- 📄 PDF upload + smart chunked text extraction
- 🤖 AI answers using relevant document context
- 🔐 Register / Login / Logout with session auth
- 📜 Full conversation history with rename & delete
- 🌙 Dark / Light theme toggle
- 📧 Real email-based password reset
- 👤 Profile details panel
- 🔍 Chat search in sidebar

---

## Setup

### 1. Clone & install dependencies

```bash
pip install -r requirements.txt
```

### 2. Configure environment

```bash
cp .env.example .env
# Edit .env with your values
```

Required variables:
- `ANTHROPIC_API_KEY` — from [console.anthropic.com](https://console.anthropic.com)
- `SECRET_KEY` — any random string
- `MAIL_USERNAME` / `MAIL_PASSWORD` — Gmail with App Password enabled
- `MAIL_SERVER` / `MAIL_PORT` — defaults to Gmail SMTP

**Gmail App Password setup:**
1. Enable 2-Step Verification on your Google account
2. Go to: Account > Security > 2-Step Verification > App passwords
3. Create a new app password and paste it in `MAIL_PASSWORD`

### 3. Run

```bash
# Load env vars and run
export $(cat .env | xargs)
python app.py
```

Or with python-dotenv auto-load:

```python
# Add to top of app.py:
from dotenv import load_dotenv
load_dotenv()
```

Then visit: **http://localhost:5000**

---

## Project Structure

```
ragchat/
├── app.py                    # Flask app, routes, models
├── requirements.txt
├── .env.example
├── templates/
│   ├── login.html            # Login page
│   ├── register.html         # Registration page
│   ├── chat.html             # Main chat interface
│   ├── reset_password.html   # Request reset email
│   └── reset_password_confirm.html  # Set new password
└── static/
    ├── css/
    │   └── style.css         # All styles (light/dark themes)
    ├── js/                   # (optional extra scripts)
    └── uploads/              # Uploaded PDFs (auto-created)
```

---

## How RAG Works Here

1. User uploads a PDF → text is extracted page by page
2. Text is split into overlapping ~800-word chunks
3. On each user question, the most relevant chunks are found via keyword scoring
4. Top chunks are sent to Claude as context along with the question
5. Claude answers based on both the document context and conversation history

---

## Customization

- **Chunk size**: Adjust `chunk_size` and `overlap` in `chunk_text()` in `app.py`
- **AI model**: Change `model` in the `/api/conversations/<uid>/chat` route
- **Email provider**: Update `MAIL_SERVER` and `MAIL_PORT` in `.env`
- **Theme colors**: All in CSS variables at the top of `style.css`
