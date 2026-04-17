import eventlet
eventlet.monkey_patch()
from flask import Flask,redirect,url_for,render_template,request,session,jsonify
from flask_socketio import SocketIO,rooms,join_room,leave_room,send,emit
import random
import os
from string import ascii_uppercase

app = Flask(__name__)
app.config['SECRET_KEY'] = 'secret_key'
socketio = SocketIO(app)
UPLOAD_FOLDER = 'static/uploads'
rooms={}
def generate_room_code(length):
    while True:
        code = ""
        for _ in range(length):
            code += random.choice(ascii_uppercase)

        if code not in rooms:
            break
    return code


@app.route('/',methods=['POST','GET'])
def home():
    if request.method == 'POST':
        name = request.form.get('name')
        code = request.form.get('code')
        join = request.form.get('join', False)
        create = request.form.get('create', False)

        if not name:
            return render_template('home.html',code=code,error="please enter name !")
        
        if join != False and not code:
            return render_template('home.html',code=code,name=name,error="please enter a room code!")
        
        room = code
        if create != False:
            room = generate_room_code(4)
            print(room)
            rooms[room] = {"members":0,"names":[], "messages": []}
        elif code not in rooms:
            return render_template('home.html',code=code,name=name,error="enter a valid room code!")

        session['room'] = room
        session['name'] = name

        return redirect(url_for('room'))
    return render_template('home.html')

@app.route('/room',methods=['GET','POST'])
def room():
    room = session.get('room')
    name = session.get('name')
    if room is None or session.get('room') is None or room not in rooms:
        return redirect(url_for('home'))
    
    return render_template('room.html',room=room,name=name,messages=rooms[room]["messages"])


@socketio.on('connect')
def connect():
    room = session.get('room')
    name = session.get('name')

    if not room or not name:
        return
    if room not in rooms:
        leave_room(room)
        return
    join_room(room)
    rooms[room]['members'] += 1
    rooms[room]['names'].append(name)
    send({"name":name,
          "message":"has entered the room",
          "members":rooms[room]['members'],
          "names":rooms[room]['names'],
          },to=room)
    print(f'{name} joined room {room}')
    


@socketio.on('disconnect')
def disconnect():
    room = session.get('room')
    name = session.get('name')

    leave_room(room)
    if room in rooms:
        rooms[room]['members'] -= 1
        rooms[room]['names'].remove(name)
        if rooms[room]['members'] <= 0:
            del rooms[room]
    send({"name":name,
          "message":"has lefted the room",
          "members":rooms[room]['members'],
          "names":rooms[room]['names'],
          },to=room)
    print(f'{name} lefted room {room}')



@socketio.on('message')
def message(data):
    room = session.get('room')
    if room not in rooms:
        return
    content = {
        "name": session.get('name'),
        "message": data["data"],
        "names": rooms[room]['names'],
        "members": rooms[room]['members']
    }
    send(content,to=room)
    rooms[room]["messages"].append(content)
    
@app.route('/upload', methods=['POST'])
def upload_file():
    name = session.get('name')
    file = request.files['image']
    
    if file:
        filename = file.filename
        path = os.path.join(UPLOAD_FOLDER, filename)
        file.save(path)

        return jsonify({
            "url": f"/static/uploads/{filename}","name":name
        })
    
@socketio.on('send_image')
def handle_image(data):
    room = data['room']
    name = session.get('name')
    
    emit('receive_image', {
        "url": data['url'],"name":name
    }, to=room)



if __name__ == '__main__':
    port = int(os.environ.get("PORT", 10000))
    socketio.run(app, host="0.0.0.0", port=port)