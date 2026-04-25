from flask import Flask, render_template
from flask_socketio import SocketIO

app = Flask(__name__)
app.config["SECRET_KEY"] = "GigihagagegaKac"

socketio = SocketIO(app, cors_allowed_origins="*", async_mode="eventlet")

from games.duel import init_duel
init_duel(app, socketio)


@app.route("/")
def home():
    return render_template("makgora-home.html")


if __name__ == "__main__":
    socketio.run(app, host="0.0.0.0", port=8000)