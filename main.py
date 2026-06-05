from flask import Flask, redirect, url_for, render_template, request, session, jsonify
from flask_socketio import SocketIO, join_room, leave_room, send, emit
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime
import os
import re
import random
from string import ascii_uppercase
import cloudinary
import cloudinary.uploader
from google.oauth2 import id_token
from google.auth.transport import requests as google_requests
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'secret_key_123_abc')

# Database configuration
db_url = os.environ.get("DATABASE_URL")
if not db_url:
    db_url = "sqlite:///chatbox.db"
    print("[WARNING] DATABASE_URL not set. Falling back to local SQLite database.")

app.config['SQLALCHEMY_DATABASE_URI'] = db_url
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# Initialize SQLAlchemy
db = SQLAlchemy(app)

# Initialize SocketIO
socketio = SocketIO(
    app,
    async_mode='threading',
    cors_allowed_origins="*"
)

# Cloudinary configuration for image uploads
cloudinary.config(
    cloud_name=os.environ.get("CLOUDINARY_CLOUD_NAME"),
    api_key=os.environ.get("CLOUDINARY_API_KEY"),
    api_secret=os.environ.get("CLOUDINARY_API_SECRET")
)

# In-memory tracking of public rooms (original feature)
rooms = {}

def generate_room_code(length):
    while True:
        code = "".join(random.choice(ascii_uppercase) for _ in range(length))
        if code not in rooms:
            break
    return code

# ==================== DATABASE MODELS ====================

class User(db.Model):
    __tablename__ = 'users'
    id = db.Column(db.Integer, primary_key=True)
    google_id = db.Column(db.String(128), unique=True, nullable=True) # null for debug users
    email = db.Column(db.String(128), unique=True, nullable=False)
    name = db.Column(db.String(128), nullable=False)
    username = db.Column(db.String(64), unique=True, nullable=True) # set onboarding
    profile_pic = db.Column(db.String(256), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            'id': self.id,
            'email': self.email,
            'name': self.name,
            'username': self.username,
            'profile_pic': self.profile_pic or '/static/images/default_avatar.png'
        }

class Message(db.Model):
    __tablename__ = 'messages'
    id = db.Column(db.Integer, primary_key=True)
    sender_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='CASCADE'), nullable=False)
    receiver_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='CASCADE'), nullable=False)
    message = db.Column(db.Text, nullable=True)
    image_url = db.Column(db.String(256), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    is_read = db.Column(db.Boolean, default=False)

    sender = db.relationship('User', foreign_keys=[sender_id], backref='sent_messages')
    receiver = db.relationship('User', foreign_keys=[receiver_id], backref='received_messages')

    def to_dict(self):
        return {
            'id': self.id,
            'sender_id': self.sender_id,
            'receiver_id': self.receiver_id,
            'message': self.message,
            'image_url': self.image_url,
            'created_at': self.created_at.isoformat(),
            'is_read': self.is_read
        }

# Automatically create tables inside application context
with app.app_context():
    try:
        db.create_all()
        print("[SUCCESS] Database tables verified/created successfully.")
    except Exception as e:
        print(f"[ERROR] Failed to connect to database or create tables: {e}")

# In-memory tracking of active connections: user_id -> set of active request.sid
online_users = {}

# ==================== OAUTH HELPERS ====================

def verify_google_token(token):
    client_id = os.environ.get("GOOGLE_CLIENT_ID")
    if not client_id:
        return None
    try:
        idinfo = id_token.verify_oauth2_token(token, google_requests.Request(), client_id)
        return idinfo
    except Exception as e:
        print(f"Google ID token verification failed: {e}")
        return None

# ==================== HTTP ROUTES ====================

@app.route('/', methods=['POST', 'GET'])
def home():
    google_client_id = os.environ.get("GOOGLE_CLIENT_ID")

    if request.method == 'POST':
        # Handled room-based anonymous login (original feature)
        name = request.form.get('name')
        code = request.form.get('code')
        join = request.form.get('join', False)
        create = request.form.get('create', False)

        if not name:
            return render_template('home.html', code=code, error="please enter name !", google_client_id=google_client_id)
        
        if join != False and not code:
            return render_template('home.html', code=code, name=name, error="please enter a room code!", google_client_id=google_client_id)
        
        room = code
        if create != False:
            room = generate_room_code(4)
            rooms[room] = {"members": 0, "names": [], "messages": []}
        elif code not in rooms:
            return render_template('home.html', code=code, name=name, error="enter a valid room code!", google_client_id=google_client_id)

        session['room'] = room
        session['name'] = name

        return redirect(url_for('room'))

    # GET request
    return render_template('home.html', google_client_id=google_client_id)

@app.route('/room', methods=['GET', 'POST'])
def room():
    # Render legacy room chat (original feature)
    room_code = session.get('room')
    name = session.get('name')
    if room_code is None or room_code not in rooms:
        return redirect(url_for('home'))
    
    return render_template('room.html', room=room_code, name=name, messages=rooms[room_code]["messages"])

@app.route('/login-google', methods=['POST'])
def login_google():
    data = request.get_json() or {}
    token = data.get('id_token')
    if not token:
        return jsonify({"success": False, "error": "Missing ID token"}), 400
        
    idinfo = verify_google_token(token)
    if not idinfo:
        return jsonify({"success": False, "error": "Invalid ID token or verification error"}), 400
        
    google_id = idinfo.get('sub')
    email = idinfo.get('email')
    name = idinfo.get('name', 'Google User')
    profile_pic = idinfo.get('picture')
    
    # Lookup or create user
    user = User.query.filter_by(google_id=google_id).first()
    if not user:
        user = User.query.filter_by(email=email).first()
        if user:
            user.google_id = google_id
            if profile_pic:
                user.profile_pic = profile_pic
        else:
            user = User(
                google_id=google_id,
                email=email,
                name=name,
                profile_pic=profile_pic,
                username=None
            )
            db.session.add(user)
        db.session.commit()
        
    session['user_id'] = user.id
    session['email'] = user.email
    session['name'] = user.name
    session['profile_pic'] = user.profile_pic
    session['username'] = user.username
    
    if user.username:
        return jsonify({"success": True, "redirect": "/chat"})
    else:
        return jsonify({"success": True, "redirect": "/username"})

@app.route('/login-debug', methods=['POST'])
def login_debug():
    email = request.form.get('email', '').strip()
    name = request.form.get('name', '').strip()
    if not email or not name:
        google_client_id = os.environ.get("GOOGLE_CLIENT_ID")
        return render_template('home.html', error="Email and Name are required for debug access.", google_client_id=google_client_id)
        
    google_id = f"debug_{email}"
    
    user = User.query.filter_by(email=email).first()
    if not user:
        user = User(
            google_id=google_id,
            email=email,
            name=name,
            profile_pic=f"https://robohash.org/{email}?set=set4",
            username=None
        )
        db.session.add(user)
        db.session.commit()
        
    session['user_id'] = user.id
    session['email'] = user.email
    session['name'] = user.name
    session['profile_pic'] = user.profile_pic
    session['username'] = user.username
    
    if user.username:
        return redirect(url_for('chat'))
    else:
        return redirect(url_for('username_onboarding'))

@app.route('/username', methods=['GET', 'POST'])
def username_onboarding():
    user_id = session.get('user_id')
    if not user_id:
        return redirect(url_for('home'))
        
    user = User.query.get(user_id)
    if not user:
        return redirect(url_for('home'))
        
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        if not username:
            return render_template('username.html', name=user.name, error="Username is required")
            
        if not re.match(r"^[a-zA-Z0-9_]{3,20}$", username):
            return render_template('username.html', name=user.name, error="Username must be 3-20 characters, alphanumeric & underscore only")
            
        # Check uniqueness
        existing_user = User.query.filter_by(username=username).first()
        if existing_user and existing_user.id != user_id:
            return render_template('username.html', name=user.name, error="Username is already taken")
            
        user.username = username
        db.session.commit()
        
        session['username'] = username
        return redirect(url_for('chat'))
        
    return render_template('username.html', name=user.name)

@app.route('/chat')
def chat():
    user_id = session.get('user_id')
    username = session.get('username')
    if not user_id:
        return redirect(url_for('home'))
    if not username:
        return redirect(url_for('username_onboarding'))
        
    user = User.query.get(user_id)
    if not user:
        return redirect(url_for('home'))
        
    return render_template(
        'chat.html',
        user_id=user.id,
        username=user.username,
        name=user.name,
        profile_pic=user.profile_pic
    )

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('home'))

@app.route('/about')
def about():
    return render_template('about.html')

# ==================== API ENDPOINTS ====================

@app.route('/api/users')
def api_users():
    curr_user_id = session.get('user_id')
    if not curr_user_id:
        return jsonify({"error": "Unauthorized"}), 401
        
    users = User.query.filter(User.username != None).all()
    user_list = []
    for u in users:
        user_list.append({
            'id': u.id,
            'username': u.username,
            'name': u.name,
            'profile_pic': u.profile_pic or f"https://robohash.org/{u.email}?set=set4",
            'online': u.id in online_users,
            'is_self': u.id == curr_user_id
        })
    return jsonify(user_list)

@app.route('/api/history/<int:other_user_id>')
def api_history(other_user_id):
    curr_user_id = session.get('user_id')
    if not curr_user_id:
        return jsonify({"error": "Unauthorized"}), 401
        
    # Mark messages from the other user as read
    unread_messages = Message.query.filter_by(
        sender_id=other_user_id,
        receiver_id=curr_user_id,
        is_read=False
    ).all()
    for m in unread_messages:
        m.is_read = True
    db.session.commit()
    
    # Retrieve complete logs
    messages = Message.query.filter(
        ((Message.sender_id == curr_user_id) & (Message.receiver_id == other_user_id)) |
        ((Message.sender_id == other_user_id) & (Message.receiver_id == curr_user_id))
    ).order_by(Message.created_at.asc()).all()
    
    return jsonify([m.to_dict() for m in messages])

@app.route('/api/unread')
def api_unread():
    curr_user_id = session.get('user_id')
    if not curr_user_id:
        return jsonify({}), 401
        
    unread = db.session.query(
        Message.sender_id, db.func.count(Message.id)
    ).filter_by(
        receiver_id=curr_user_id,
        is_read=False
    ).group_by(Message.sender_id).all()
    
    return jsonify({sender_id: count for sender_id, count in unread})

@app.route('/upload', methods=['POST'])
def upload_file():
    if 'image' not in request.files:
        return {"error": "No file"}, 400
    file = request.files['image']
    if file.filename == "":
        return {"error": "Empty file"}, 400
        
    # Check if Cloudinary credentials are set
    if os.environ.get("CLOUDINARY_CLOUD_NAME"):
        try:
            result = cloudinary.uploader.upload(file)
            return {"url": result["secure_url"]}
        except Exception as e:
            print(f"Cloudinary upload error: {e}")
            
    # Local fallback upload folder in workspace
    upload_folder = os.path.join(app.root_path, 'static', 'uploads')
    if not os.path.exists(upload_folder):
        os.makedirs(upload_folder)
        
    filename = f"{int(datetime.utcnow().timestamp())}_{file.filename}"
    filepath = os.path.join(upload_folder, filename)
    file.save(filepath)
    return {"url": f"/static/uploads/{filename}"}

# ==================== SOCKET.IO EVENTS ====================

@socketio.on('connect')
def connect():
    room = session.get('room')
    name = session.get('name')
    user_id = session.get('user_id')
    
    # 1. Check room-based connection (original feature)
    if room and name:
        if room not in rooms:
            return False
        join_room(room)
        rooms[room]['members'] += 1
        rooms[room]['names'].append(name)
        send({"name": name,
              "message": "has entered the room",
              "members": rooms[room]['members'],
              "names": rooms[room]['names'],
              }, to=room)
        print(f"[SOCKET] Room client {name} joined room {room}")
        
    # 2. Check private chat connection
    if user_id:
        join_room(f"user_{user_id}")
        if user_id not in online_users:
            online_users[user_id] = set()
        online_users[user_id].add(request.sid)
        
        # Broadcast status change to everyone
        emit('user_status', {'user_id': user_id, 'status': 'online'}, broadcast=True)
        print(f"[SOCKET] Secure client {user_id} connected. Connection count: {len(online_users[user_id])}")

@socketio.on('disconnect')
def disconnect():
    room = session.get('room')
    name = session.get('name')
    user_id = session.get('user_id')
    
    # 1. Handle room chat disconnection (original feature)
    if room and name and room in rooms:
        leave_room(room)
        rooms[room]['members'] -= 1
        if name in rooms[room]['names']:
            rooms[room]['names'].remove(name)
        members = rooms[room]['members']
        names = rooms[room]['names']
        
        send({
            "name": name,
            "message": "has left the room",
            "members": members,
            "names": names,
        }, to=room)
        
        if members <= 0:
            if room in rooms:
                del rooms[room]
            print(f"[SOCKET] Room {room} deleted (0 members).")
        else:
            print(f"[SOCKET] Room client {name} left room {room}")
            
    # 2. Handle private chat disconnection
    if user_id and user_id in online_users:
        online_users[user_id].discard(request.sid)
        if not online_users[user_id]:
            del online_users[user_id]
            # Broadcast status change to everyone
            emit('user_status', {'user_id': user_id, 'status': 'offline'}, broadcast=True)
            print(f"[SOCKET] Secure client {user_id} disconnected completely.")
        else:
            print(f"[SOCKET] Secure client {user_id} closed a tab. Remaining: {len(online_users[user_id])}")

# Original public room messaging events
@socketio.on('message')
def message(data):
    room = session.get('room')
    if not room or room not in rooms:
        return
    content = {
        "name": session.get('name'),
        "message": data["data"],
        "names": rooms[room]['names'],
        "members": rooms[room]['members']
    }
    send(content, to=room)
    rooms[room]["messages"].append(content)
    
@socketio.on('send_image')
def handle_image(data):
    room = session.get('room')
    name = session.get('name')
    if not room or room not in rooms:
        return
    content = {
        "name": name,
        "image": data['url']
    }
    rooms[room]["messages"].append(content)
    emit('receive_image', content, to=room)

# Secure private messaging events
@socketio.on('private_message')
def private_message(data):
    sender_id = session.get('user_id')
    if not sender_id:
        return
        
    receiver_id = data.get('receiver_id')
    message_text = data.get('message', '').strip()
    image_url = data.get('image_url')
    
    if not receiver_id:
        return
    if not message_text and not image_url:
        return
        
    # Persist in DB
    msg = Message(
        sender_id=sender_id,
        receiver_id=receiver_id,
        message=message_text,
        image_url=image_url,
        is_read=False
    )
    db.session.add(msg)
    db.session.commit()
    
    payload = msg.to_dict()
    # Send to receiver's room
    emit('new_private_message', payload, to=f"user_{receiver_id}")
    # Sync with other active tabs of sender
    emit('new_private_message', payload, to=f"user_{sender_id}")


if __name__ == '__main__':
    port = int(os.environ.get("PORT", 10000))
    socketio.run(app, host="0.0.0.0", port=port, debug=True, allow_unsafe_werkzeug=True)