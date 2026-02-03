# games/duel/routes.py
from flask import Blueprint, render_template

duel_bp = Blueprint("duel", __name__)

@duel_bp.route("/duel")
def duel_page():
    return render_template("duel.html")
