from flask import Flask, render_template, request, jsonify, session, redirect, url_for
from flask_sqlalchemy import SQLAlchemy
from flask_cors import CORS
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
import os, json, uuid, re, secrets, datetime
import PyPDF2
from google import genai
from google.genai import types

# Explicitly load environment variables from the .env file
from dotenv import load_dotenv
load_dotenv()

# Smart Backup Scanner
if not os.environ.get('GEMINI_API_KEY'):
    for fallback_file in ['.env', '.env.txt', 'env.txt']:
        if os.path.exists(fallback_file):
            try:
                with open(fallback_file, 'r') as f:
                    for line in f:
                        if 'GEMINI_API_KEY' in line and '=' in line:
                            extracted_key = line.split('=', 1)[1].strip().strip('"').strip("'")
                            if extracted_key:
                                os.environ['GEMINI_API_KEY'] = extracted_key
                                break
            except Exception:
                pass

app = Flask(__name__)
CORS(app)

app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'ragchat-secret-2024-xk92')
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///ragchat.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['UPLOAD_FOLDER'] = 'static/uploads'
app.config['MAX_CONTENT_LENGTH'] = 2 * 1024 * 1024  # 2MB limit for free tier RAM

# Ensure upload folder exists
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

import os
from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail

db = SQLAlchemy(app)

def send_email(to_email, subject, body):
    message = Mail(
        from_email='your-verified-email@example.com',
        to_emails=to_email,
        subject=subject,
        html_content=body
    )
    try:
        sg = SendGridAPIClient(os.environ.get('SENDGRID_API_KEY'))
        sg.send(message)
    except Exception as e:
        print(f"SendGrid Error: {e}")

ALLOWED_EXTENSIONS = {'pdf'}

# â”€â”€â”€ Models â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.datetime.utcnow)
    reset_token = db.Column(db.String(100), nullable=True)
    reset_token_expiry = db.Column(db.DateTime, nullable=True)
    conversations = db.relationship('Conversation', backref='user', lazy=True, cascade='all, delete-orphan')

class Conversation(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    uid = db.Column(db.String(36), default=lambda: str(uuid.uuid4()), unique=True)
    title = db.Column(db.String(200), default='New Chat')
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow)
    pdf_filename = db.Column(db.String(300), nullable=True)
    pdf_text = db.Column(db.Text, nullable=True)
    messages = db.relationship('ChatMessage', backref='conversation', lazy=True, cascade='all, delete-orphan')

class ChatMessage(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    conversation_id = db.Column(db.Integer, db.ForeignKey('conversation.id'), nullable=False)
    role = db.Column(db.String(20), nullable=False)  # 'user' or 'assistant'
    content = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.datetime.utcnow)

# â”€â”€â”€ Helpers â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def extract_pdf_text(filepath):
    try:
        with open(filepath, 'rb') as f:
            reader = PyPDF2.PdfReader(f)
            # Limit to first 50 pages to prevent OOM
            pages_to_read = min(len(reader.pages), 50)
            text_blocks = []
            for i in range(pages_to_read):
                page_text = reader.pages[i].extract_text()
                if page_text:
                    text_blocks.append(page_text)
            return "\n".join(text_blocks)
    except Exception as e:
        return f"Error extracting PDF: {str(e)}"

def chunk_text(text, chunk_size=800, overlap=100):
    words = text.split()
    chunks = []
    i = 0
    while i < len(words):
        chunk = ' '.join(words[i:i+chunk_size])
        chunks.append(chunk)
        i += chunk_size - overlap
    return chunks

def find_relevant_chunks(query, chunks, top_k=4):
    query_words = set(query.lower().split())
    scored = []
    for i, chunk in enumerate(chunks):
        chunk_words = set(chunk.lower().split())
        score = len(query_words & chunk_words)
        scored.append((score, i, chunk))
    scored.sort(reverse=True)
    return [c[2] for c in scored[:top_k]]

def get_current_user():
    if 'user_id' not in session:
        return None
    return db.session.get(User, session['user_id'])

# â”€â”€â”€ Auth Routes â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

@app.route('/')
def index():
    if 'user_id' in session:
        return redirect(url_for('chat_page'))
    return render_template('index.html')

@app.route('/login')
def login_page():
    if 'user_id' in session:
        return redirect(url_for('chat_page'))
    return render_template('login.html')

@app.route('/register')
def register_page():
    if 'user_id' in session:
        return redirect(url_for('chat_page'))
    return render_template('register.html')

@app.route('/chat')
def chat_page():
    if 'user_id' not in session:
        return redirect(url_for('login_page'))
    return render_template('chat.html')

@app.route('/reset-password')
def reset_password_page():
    return render_template('reset_password.html')

@app.route('/reset-password/<token>')
def reset_password_confirm_page(token):
    return render_template('reset_password_confirm.html', token=token)

@app.route('/api/register', methods=['POST'])
def api_register():
    data = request.get_json()
    username = data.get('username', '').strip()
    email = data.get('email', '').strip().lower()
    password = data.get('password', '')

    if not username or not email or not password:
        return jsonify({'error': 'All fields required'}), 400
    if len(password) < 6:
        return jsonify({'error': 'Password must be at least 6 characters'}), 400
    if not re.match(r'^[^@]+@[^@]+\.[^@]+$', email):
        return jsonify({'error': 'Invalid email address'}), 400
    if User.query.filter_by(username=username).first():
        return jsonify({'error': 'Username already taken'}), 409
    if User.query.filter_by(email=email).first():
        return jsonify({'error': 'Email already registered'}), 409

    user = User(username=username, email=email, password_hash=generate_password_hash(password))
    db.session.add(user)
    db.session.commit()
    session['user_id'] = user.id
    session['username'] = user.username
    return jsonify({'success': True, 'username': user.username})

@app.route('/api/login', methods=['POST'])
def api_login():
    data = request.get_json()
    identifier = data.get('identifier', '').strip()
    password = data.get('password', '')

    user = User.query.filter_by(email=identifier.lower()).first() or \
           User.query.filter_by(username=identifier).first()

    if not user or not check_password_hash(user.password_hash, password):
        return jsonify({'error': 'Invalid credentials'}), 401

    session['user_id'] = user.id
    session['username'] = user.username
    return jsonify({'success': True, 'username': user.username})

@app.route('/api/logout', methods=['POST'])
def api_logout():
    session.clear()
    return jsonify({'success': True})

@app.route('/api/me')
def api_me():
    user = get_current_user()
    if not user:
        return jsonify({'error': 'Not authenticated'}), 401
    return jsonify({'id': user.id, 'username': user.username, 'email': user.email,
                    'created_at': user.created_at.isoformat()})

@app.route('/api/request-reset', methods=['POST'])
def request_reset():
    data = request.get_json()
    email = data.get('email', '').strip().lower()
    user = User.query.filter_by(email=email).first()

    if not user:
        return jsonify({'error': 'This email is not registered.'}), 404

    token = secrets.token_urlsafe(32)
    user.reset_token = token
    user.reset_token_expiry = datetime.datetime.utcnow() + datetime.timedelta(hours=1)
    db.session.commit()

    reset_url = url_for('reset_password_confirm_page', token=token, _external=True)

    email_html = f"""
    <div style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto; border: 1px solid #e2ddd6; border-radius: 8px; overflow: hidden; color: #111;">
        <div style="background-color: #111; color: #fff; padding: 20px; text-align: center;">
            <h2 style="margin: 0; font-family: 'Cormorant Garamond', Georgia, serif; font-style: italic;">RAGChat Password Reset</h2>
        </div>
        <div style="padding: 30px; background-color: #f7f6f3; text-align: center;">
            <p style="font-size: 16px; color: #444; margin-bottom: 25px;">You requested a password reset for your RAGChat account.</p>
            <a href="{reset_url}" style="display: inline-block; background-color: #7c3aed; color: #fff; text-decoration: none; padding: 12px 24px; border-radius: 8px; font-weight: bold; font-size: 16px;">Reset Password</a>
            <p style="font-size: 13px; color: #666; margin-top: 25px;">If you didn't request this, you can safely ignore this email. This link will expire in 1 hour.</p>
        </div>
    </div>
    """

    try:
        send_email(
            to_email=user.email,
            subject='RAGChat — Reset Your Password',
            body=email_html
        )
    except Exception as e:
        print(f"Mail error: {e}")
        return jsonify({'error': 'Failed to send the reset email. Please check server mail configuration.'}), 500

    return jsonify({'success': True, 'message': 'Password reset link sent! Check your inbox.'})

@app.route('/api/reset-password', methods=['POST'])
def do_reset_password():
    data = request.get_json()
    token = data.get('token', '')
    new_password = data.get('password', '')

    if len(new_password) < 6:
        return jsonify({'error': 'Password must be at least 6 characters'}), 400

    user = User.query.filter_by(reset_token=token).first()
    if not user or not user.reset_token_expiry or \
       user.reset_token_expiry < datetime.datetime.utcnow():
        return jsonify({'error': 'Invalid or expired reset link'}), 400

    user.password_hash = generate_password_hash(new_password)
    user.reset_token = None
    user.reset_token_expiry = None
    db.session.commit()
    return jsonify({'success': True})

# --- Contact Route ---

@app.route('/api/contact', methods=['POST'])
def api_contact():
    data    = request.get_json()
    name    = data.get('name', '').strip()
    email   = data.get('email', '').strip()
    subject = data.get('subject', '').strip()
    message = data.get('message', '').strip()
    if not all([name, email, subject, message]):
        return jsonify({'error': 'All fields are required'}), 400
    try:
        log_dir = os.path.join(os.path.dirname(__file__), 'instance')
        os.makedirs(log_dir, exist_ok=True)
        with open(os.path.join(log_dir, 'contact_messages.txt'), 'a', encoding='utf-8') as f:
            f.write("=" * 50 + "\n")
            f.write(f"From: {name} <{email}>\nSubject: {subject}\nTime: {datetime.datetime.utcnow().isoformat()}\nMessage:\n{message}\n\n")
    except Exception as le:
        print(f"Contact log error: {le}")
    try:
        owner = os.environ.get('MAIL_USERNAME', '')
        if owner:
            # Build a beautifully styled HTML email
            email_html = f"""
            <div style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto; border: 1px solid #e2ddd6; border-radius: 8px; overflow: hidden; color: #111;">
                <div style="background-color: #111; color: #fff; padding: 20px; text-align: center;">
                    <h2 style="margin: 0; font-family: Georgia, serif; font-style: italic;">RAGChat Contact Form</h2>
                </div>
                <div style="padding: 30px; background-color: #f7f6f3;">
                    <p style="margin: 0 0 10px 0; font-size: 14px; color: #666;">You've received a new message via the RAGChat website contact form.</p>
                    <table style="width: 100%; border-collapse: collapse; margin-top: 20px; background: #fff; border-radius: 6px; overflow: hidden; box-shadow: 0 2px 4px rgba(0,0,0,0.05);">
                        <tr>
                            <td style="padding: 12px 15px; border-bottom: 1px solid #eee; width: 100px; font-weight: bold; color: #444;">Name:</td>
                            <td style="padding: 12px 15px; border-bottom: 1px solid #eee;">{name}</td>
                        </tr>
                        <tr>
                            <td style="padding: 12px 15px; border-bottom: 1px solid #eee; font-weight: bold; color: #444;">Email:</td>
                            <td style="padding: 12px 15px; border-bottom: 1px solid #eee;"><a href="mailto:{email}" style="color: #7c3aed; text-decoration: none;">{email}</a></td>
                        </tr>
                        <tr>
                            <td style="padding: 12px 15px; border-bottom: 1px solid #eee; font-weight: bold; color: #444;">Subject:</td>
                            <td style="padding: 12px 15px; border-bottom: 1px solid #eee;">{subject}</td>
                        </tr>
                        <tr>
                            <td colspan="2" style="padding: 15px; font-weight: bold; color: #444; background: #fdfdfd;">Message:</td>
                        </tr>
                        <tr>
                            <td colspan="2" style="padding: 0 15px 15px 15px; line-height: 1.6; white-space: pre-wrap; background: #fdfdfd; font-family: inherit;">{message}</td>
                        </tr>
                    </table>
                </div>
                <div style="background-color: #f0ede8; padding: 15px; text-align: center; font-size: 12px; color: #888;">
                    This email was automatically generated from the RAGChat contact form.
                </div>
            </div>
            """
            send_email(
                to_email=owner,
                subject=f'RAGChat Contact: {subject}',
                body=email_html
            )
    except Exception as e:
        print(f"SMTP Error: {e}")
        return jsonify({'error': 'Email service currently unavailable'}), 503
    return jsonify({'success': True})
# â”€â”€â”€ Conversation Routes â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

@app.route('/api/conversations', methods=['GET'])
def get_conversations():
    user = get_current_user()
    if not user:
        return jsonify({'error': 'Not authenticated'}), 401
    convs = Conversation.query.filter_by(user_id=user.id).order_by(Conversation.updated_at.desc()).all()
    return jsonify([{
        'uid': c.uid, 'title': c.title,
        'created_at': c.created_at.isoformat(),
        'updated_at': c.updated_at.isoformat(),
        'has_pdf': bool(c.pdf_filename)
    } for c in convs])

@app.route('/api/conversations', methods=['POST'])
def create_conversation():
    user = get_current_user()
    if not user:
        return jsonify({'error': 'Not authenticated'}), 401
    conv = Conversation(user_id=user.id, title='New Chat')
    db.session.add(conv)
    db.session.commit()
    return jsonify({'uid': conv.uid, 'title': conv.title})

@app.route('/api/conversations/<uid>', methods=['GET'])
def get_conversation(uid):
    user = get_current_user()
    if not user:
        return jsonify({'error': 'Not authenticated'}), 401
    conv = Conversation.query.filter_by(uid=uid, user_id=user.id).first()
    if not conv:
        return jsonify({'error': 'Not found'}), 404
    messages = [{'role': m.role, 'content': m.content, 'created_at': m.created_at.isoformat()}
                for m in conv.messages]
    return jsonify({'uid': conv.uid, 'title': conv.title, 'messages': messages,
                    'has_pdf': bool(conv.pdf_filename), 'pdf_filename': conv.pdf_filename})

@app.route('/api/conversations/<uid>', methods=['PATCH'])
def rename_conversation(uid):
    user = get_current_user()
    if not user:
        return jsonify({'error': 'Not authenticated'}), 401
    conv = Conversation.query.filter_by(uid=uid, user_id=user.id).first()
    if not conv:
        return jsonify({'error': 'Not found'}), 404
    data = request.get_json()
    conv.title = data.get('title', conv.title)[:200]
    db.session.commit()
    return jsonify({'success': True})

@app.route('/api/conversations/<uid>', methods=['DELETE'])
def delete_conversation(uid):
    user = get_current_user()
    if not user:
        return jsonify({'error': 'Not authenticated'}), 401
    conv = Conversation.query.filter_by(uid=uid, user_id=user.id).first()
    if not conv:
        return jsonify({'error': 'Not found'}), 404
    db.session.delete(conv)
    db.session.commit()
    return jsonify({'success': True})

@app.route('/api/conversations/<uid>/upload', methods=['POST'])
def upload_pdf(uid):
    user = get_current_user()
    if not user:
        return jsonify({'error': 'Not authenticated'}), 401
    conv = Conversation.query.filter_by(uid=uid, user_id=user.id).first()
    if not conv:
        return jsonify({'error': 'Conversation not found'}), 404

    if 'file' not in request.files:
        return jsonify({'error': 'No file provided'}), 400
    file = request.files['file']
    if not file or not allowed_file(file.filename):
        return jsonify({'error': 'Only PDF files allowed'}), 400

    filename = secure_filename(f"{uid}_{file.filename}")
    filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
    file.save(filepath)

    text = extract_pdf_text(filepath)
    chunks = chunk_text(text)

    conv.pdf_filename = file.filename
    conv.pdf_text = text
    if conv.title == 'New Chat':
        conv.title = file.filename.replace('.pdf', '')[:60]
    db.session.commit()

    return jsonify({
        'success': True,
        'filename': file.filename,
        'chunks': len(chunks),
        'pages': text.count('\n\n') + 1
    })

# â”€â”€â”€ Chat â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

@app.route('/api/conversations/<uid>/chat', methods=['POST'])
def chat(uid):
    user = get_current_user()
    if not user:
        return jsonify({'error': 'Not authenticated'}), 401
    conv = Conversation.query.filter_by(uid=uid, user_id=user.id).first()
    if not conv:
        return jsonify({'error': 'Conversation not found'}), 404

    data = request.get_json()
    user_message = data.get('message', '').strip()
    if not user_message:
        return jsonify({'error': 'Empty message'}), 400

    context_block = ""
    if conv.pdf_text:
        chunks = chunk_text(conv.pdf_text)
        relevant = find_relevant_chunks(user_message, chunks)
        context_block = "\n\n---\nRelevant PDF Context:\n" + "\n\n".join(relevant)

    history = ChatMessage.query.filter_by(conversation_id=conv.id).order_by(ChatMessage.created_at.desc()).limit(10).all()
    history.reverse()

    user_msg = ChatMessage(conversation_id=conv.id, role='user', content=user_message)
    db.session.add(user_msg)

    system_prompt = """You are RAGChat, an intelligent AI assistant that helps users analyze and understand PDF documents.
When PDF context is provided, answer questions based on that content accurately and helpfully.
If no PDF context is relevant, answer from general knowledge.
Be concise, clear, and helpful. Format responses with markdown when appropriate."""

    try:
        client = genai.Client(api_key=os.environ.get('GEMINI_API_KEY', ''))
        contents = []
        for m in history:
            role = "user" if m.role == "user" else "model"
            contents.append(types.Content(role=role, parts=[types.Part.from_text(text=m.content)]))
        
        contents.append(types.Content(role="user", parts=[types.Part.from_text(text=user_message + context_block)]))

        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=contents,
            config=types.GenerateContentConfig(
                system_instruction=system_prompt,
                max_output_tokens=1500,
            )
        )
        assistant_reply = response.text
    except Exception as e:
        error_str = str(e).lower()
        if "429" in error_str or "resource_exhausted" in error_str or "quota" in error_str:
            assistant_reply = "your quote is over come back and try again after 5 mins"
        else:
            assistant_reply = f"I encountered an error processing your request. Please ensure GEMINI_API_KEY is set. Error: {str(e)}"

    asst_msg = ChatMessage(conversation_id=conv.id, role='assistant', content=assistant_reply)
    db.session.add(asst_msg)

    if len(conv.messages) <= 2 and conv.title in ['New Chat', conv.pdf_filename]:
        words = user_message.split()[:6]
        conv.title = ' '.join(words) + ('...' if len(words) >= 6 else '')

    conv.updated_at = datetime.datetime.utcnow()
    db.session.commit()

    return jsonify({'reply': assistant_reply})

# â”€â”€â”€ Init â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

with app.app_context():
    db.create_all()

if __name__ == '__main__':
    app.run(debug=True, port=5000)