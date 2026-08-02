# games/duel/__init__.py
from .routes import duel_bp
from .sockets import register_duel_socket_handlers
from .state import start_lifecycle_sweeper

def init_duel(app, socketio):
    app.register_blueprint(duel_bp)
    register_duel_socket_handlers(socketio)
    start_lifecycle_sweeper(socketio)
