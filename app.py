from flask import Flask, render_template, request, redirect, url_for, session
from flask_socketio import SocketIO, join_room, leave_room, send, emit
import random
import string

app = Flask(__name__)
app.config['SECRET_KEY'] = ''.join(random.choices(string.ascii_letters + string.digits, k=32))
socketio = SocketIO(app, async_mode='threading')

# In-memory storage for rooms and their state
# rooms = {
# 'room_id': {
# 'host_sid': 'session_id_of_host',
# 'viewers': {'sid1': 'Right Pace', 'sid2': 'Too Fast'},
# 'pace_counts': {'Too Fast': 1, 'Too Slow': 0, 'Right Pace': 1}
# }
# }
rooms = {}

def generate_room_id(length=6):
    """Generate a unique room ID."""
    while True:
        room_id = ''.join(random.choices(string.ascii_uppercase + string.digits, k=length))
        if room_id not in rooms:
            return room_id

@app.route('/', methods=['GET', 'POST'])
def index():
    if request.method == 'POST':
        role = request.form.get('role')
        if role == 'host':
            room_id = generate_room_id()
            session['room_id'] = room_id
            session['role'] = 'host'
            rooms[room_id] = {
                'host_sid': None, 
                'viewers': {},
                'pace_counts': {'Too Fast': 0, 'Too Slow': 0, 'Right Pace': 0},
                'alert_settings': {'threshold': 70, 'min_viewers': 5} # Default alert settings
            }
            return redirect(url_for('host_settings_view', room_id=room_id))
        elif role == 'viewer':
            room_id_input = request.form.get('room_id_input', '').upper()
            if room_id_input and room_id_input in rooms:
                session['room_id'] = room_id_input
                session['role'] = 'viewer'
                return redirect(url_for('room_view', room_id=room_id_input))
            else:
                return render_template('index.html', error="Invalid Room ID. Please try again or check with the host.")
    return render_template('index.html')

@app.route('/room/<room_id>/settings', methods=['GET', 'POST'])
def host_settings_view(room_id):
    if 'role' not in session or session.get('role') != 'host' or session.get('room_id') != room_id:
        return redirect(url_for('index'))
    if room_id not in rooms:
        session.clear()
        return redirect(url_for('index', error="Room does not exist."))

    if request.method == 'POST':
        try:
            threshold = int(request.form.get('threshold_percentage'))
            min_viewers = int(request.form.get('min_viewers_for_alert'))

            if not (1 <= threshold <= 100 and min_viewers >= 1):
                raise ValueError("Invalid input values.")

            rooms[room_id]['alert_settings']['threshold'] = threshold
            rooms[room_id]['alert_settings']['min_viewers'] = min_viewers
            print(f"Room {room_id} alert settings updated: {rooms[room_id]['alert_settings']}")
            return redirect(url_for('room_view', room_id=room_id))
        except (ValueError, TypeError):
            return render_template('host_settings.html', 
                                   room_id=room_id, 
                                   default_threshold=rooms[room_id]['alert_settings'].get('threshold', 70), 
                                   default_min_viewers=rooms[room_id]['alert_settings'].get('min_viewers', 5),
                                   error="Invalid input. Please enter valid numbers.")
    
    # For GET request
    return render_template('host_settings.html', 
                           room_id=room_id, 
                           default_threshold=rooms[room_id]['alert_settings'].get('threshold', 70),
                           default_min_viewers=rooms[room_id]['alert_settings'].get('min_viewers', 5))

@app.route('/room/<room_id>')
def room_view(room_id):
    if 'role' not in session or session.get('room_id') != room_id:
        return redirect(url_for('index'))
    
    role = session['role']
    if room_id not in rooms:
        # If room somehow got deleted or was never created properly
        session.clear()
        return redirect(url_for('index', error="Room does not exist."))

    if role == 'host' and 'threshold' not in rooms[room_id]['alert_settings']:
        # This case should ideally not be hit if settings page is mandatory
        # but as a fallback, redirect to settings if they are somehow missing.
        return redirect(url_for('host_settings_view', room_id=room_id))

    return render_template(f'{role}.html', room_id=room_id)

@socketio.on('join')
def on_join(data):
    room_id = data['room_id']
    user_role = session.get('role') # Get role from session

    if room_id not in rooms:
        emit('room_error', {'error': 'Room does not exist.'})
        return

    join_room(room_id)
    
    if user_role == 'host':
        rooms[room_id]['host_sid'] = request.sid
        emit('host_joined', {'room_id': room_id, 'host_sid': request.sid}, to=room_id)
        print(f"Host {request.sid} joined room {room_id}")
    elif user_role == 'viewer':
        rooms[room_id]['viewers'][request.sid] = 'Right Pace' # Default pace
        update_pace_counts(room_id, None, 'Right Pace') # Increment Right Pace
        emit('viewer_joined', {'sid': request.sid, 'viewer_count': len(rooms[room_id]['viewers'])}, to=room_id)
        # Send current aggregated counts to the newly joined viewer and the host
        emit_pace_update(room_id) 
        print(f"Viewer {request.sid} joined room {room_id}. Total viewers: {len(rooms[room_id]['viewers'])}")

    print(f"Current room status: {rooms[room_id]}")


def update_pace_counts(room_id, old_vote, new_vote):
    """Helper function to update pace_counts for a room."""
    if room_id not in rooms:
        return
    
    counts = rooms[room_id]['pace_counts']
    if old_vote: # if not None, means viewer is changing vote
        counts[old_vote] = max(0, counts[old_vote] - 1)
    counts[new_vote] = counts.get(new_vote, 0) + 1
    print(f"Pace counts for room {room_id} updated: {counts}")


def emit_pace_update(room_id):
    """Emits the current pace distribution to everyone in the room."""
    if room_id in rooms:
        pace_data = rooms[room_id]['pace_counts']
        total_viewers = len(rooms[room_id]['viewers'])
        alert_settings = rooms[room_id].get('alert_settings', {'threshold': 70, 'min_viewers': 5}) # Fallback
        
        data_to_send = {
            'counts': pace_data,
            'total_viewers': total_viewers
        }
        
        emit('pace_update', data_to_send, to=room_id)
        print(f"Emitted pace_update for room {room_id}: {data_to_send}")
        
        if total_viewers > 0:
            fast_percentage = (pace_data.get('Too Fast', 0) / total_viewers) * 100
            slow_percentage = (pace_data.get('Too Slow', 0) / total_viewers) * 100
            
            threshold_percentage = alert_settings['threshold']
            min_viewers_for_alert = alert_settings['min_viewers']

            message = None
            if total_viewers >= min_viewers_for_alert:
                if fast_percentage >= threshold_percentage and slow_percentage >= threshold_percentage:
                    message = f"Mixed feedback: ~{fast_percentage:.0f}% find it too fast, ~{slow_percentage:.0f}% too slow."
                elif fast_percentage >= threshold_percentage:
                    message = f"{fast_percentage:.0f}% of the audience finds the pace TOO FAST!"
                elif slow_percentage >= threshold_percentage:
                    message = f"{slow_percentage:.0f}% of the audience finds the pace TOO SLOW!"
            
            if message and rooms[room_id].get('host_sid'):
                emit('host_alert', {'message': message}, to=rooms[room_id]['host_sid'])
                print(f"Alert sent to host of room {room_id} with custom settings: {message}")


@socketio.on('send_pace')
def on_send_pace(data):
    room_id = session.get('room_id')
    new_pace = data['pace']
    viewer_sid = request.sid

    if not room_id or room_id not in rooms:
        emit('room_error', {'error': 'Invalid session or room.'})
        return

    if viewer_sid not in rooms[room_id]['viewers']:
        # This could happen if a viewer sends a pace vote before fully 'joining' or if state is lost
        # For robustness, could add them here, or simply log and ignore.
        print(f"Warning: Pace vote from {viewer_sid} in room {room_id} but viewer not in room's viewer list.")
        # Optionally, add the viewer if they are not in the list and try to proceed
        # rooms[room_id]['viewers'][viewer_sid] = new_pace 
        # update_pace_counts(room_id, None, new_pace)
        # For now, we'll just ignore if they are not formally in the viewer list
        return


    old_pace = rooms[room_id]['viewers'].get(viewer_sid)
    
    if old_pace == new_pace: # Vote hasn't changed
        return

    rooms[room_id]['viewers'][viewer_sid] = new_pace
    update_pace_counts(room_id, old_pace, new_pace)
    emit_pace_update(room_id)
    print(f"Viewer {viewer_sid} in room {room_id} changed pace to {new_pace}. Counts: {rooms[room_id]['pace_counts']}")


@socketio.on('disconnect')
def on_disconnect():
    print(f"Client {request.sid} disconnected.")
    # Find which room the disconnected client was in and if they were a host or viewer
    disconnected_sid = request.sid
    room_to_update = None
    
    for room_id, data in list(rooms.items()): # Iterate over a copy if modifying dict
        if data['host_sid'] == disconnected_sid:
            print(f"Host {disconnected_sid} disconnected from room {room_id}. Room will be closed.")
            emit('host_disconnected', {'message': 'Host has disconnected. The room is now closed.'}, to=room_id)
            # Clean up the room
            # Notify all viewers in the room
            for viewer_sid in data['viewers']:
                # Optionally, could force disconnect clients or redirect them
                pass # Viewers will get 'host_disconnected' event
            del rooms[room_id]
            room_to_update = None # No further updates needed for this room
            break 
        elif disconnected_sid in data['viewers']:
            print(f"Viewer {disconnected_sid} disconnected from room {room_id}.")
            old_vote = data['viewers'].pop(disconnected_sid) # Remove viewer and get their last vote
            update_pace_counts(room_id, old_vote, None) # Decrement count for their last vote
            room_to_update = room_id
            break # Assuming a SID can only be in one room as a viewer

    if room_to_update:
        # Update remaining participants about the change in viewer count and pace distribution
        emit_pace_update(room_to_update)
        # Notify host and other viewers that a viewer left
        emit('viewer_left', {'sid': disconnected_sid, 'viewer_count': len(rooms[room_to_update]['viewers'])}, to=room_to_update)


if __name__ == '__main__':
    socketio.run(app, host='0.0.0.0', port=5000) 