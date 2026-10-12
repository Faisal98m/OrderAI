import uuid
import os
import tempfile
import requests
from openai import OpenAI
from flask import Flask, request, jsonify, render_template, session
from orderai.core.agent import run_agent, execute_tool
from orderai.services.database import init_db, get_all_orders, update_order_status
from orderai.paths import REPOSITORY_ROOT

client = OpenAI(
    api_key=os.getenv("OPENAI_API_KEY")
)


app = Flask(__name__, root_path=str(REPOSITORY_ROOT))

app.secret_key = os.getenv("FLASK_SECRET_KEY") or os.urandom(32)
app.config.update(
    DEMO_ENABLED=os.getenv("ORDERAI_DEMO_ENABLED") == "1",
    DEMO_ACCESS_CODE=os.getenv("ORDERAI_DEMO_ACCESS_CODE", ""),
    DEMO_CALL_SECONDS=600,
    DEMO_IDLE_SECONDS=45,
    DEMO_RETAIN_SECONDS=1800,
    DEMO_MAX_ACTIVE=8,
    MAX_CONTENT_LENGTH=(2 * 1024 * 1024 if os.getenv("ORDERAI_DEMO_ENABLED") == "1" else None),
)
from orderai.services.demo import bp as demo_blueprint, demo_home, demo_tool, demo_credential
app.register_blueprint(demo_blueprint)
app.config['MENU_EDITOR_ENABLED'] = os.getenv('ORDERAI_MENU_EDITOR_ENABLED') == '1'

from orderai.services.menu_management import bp as menu_management_blueprint
app.register_blueprint(menu_management_blueprint)



@app.get("/healthz")
def health():
    # No model requests, credentials, database access or sessions.
    return jsonify(status="ok")


@app.route("/")
def home():
    if app.config["DEMO_ENABLED"]:
        return demo_home()

    restaurant_id = request.args.get(
        "restaurant",
        session.get("restaurant_id", "sanis")
    )

    allowed_restaurants = {
        "sanis",
        "burger_and_sauce"
    }

    if restaurant_id not in allowed_restaurants:
        return "Unknown restaurant", 404

    session["restaurant_id"] = restaurant_id

    return render_template("legacy_index.html")


@app.route("/chat", methods=["POST"])
def chat():
    if app.config["DEMO_ENABLED"]:
        return jsonify(error="Text chat is disabled in the voice demo."), 403

    if "session_id" not in session:
        session["session_id"] = str(uuid.uuid4())

    data = request.get_json()

    if not data or "message" not in data:
        return jsonify({
            "error": "A message is required."
        }), 400

    message = data["message"]

    previous_response_id = session.get(
        "previous_response_id"
    )

    # Each customer message is a new turn
    turn_id = session.get("turn_id", 0) + 1
    session["turn_id"] = turn_id

    restaurant_id = session.get(
        "restaurant_id",
        "sanis"
    )

    session_id = session["session_id"]

    response, new_response_id = run_agent(
        message,
        previous_response_id,
        turn_id,
        restaurant_id,
        session_id
    )

    session["previous_response_id"] = (
        new_response_id
    )

    return jsonify({
        "message": response
    })

@app.route("/transcribe", methods=["POST"])
def transcribe():
    if app.config["DEMO_ENABLED"]:
        return jsonify(error="File transcription is disabled in the voice demo."), 403

    if "audio" not in request.files:
        return jsonify({
            "error": "No audio file received."
        }), 400

    audio = request.files["audio"]

    print("Received filename:", audio.filename)
    print("Received MIME type:", audio.content_type)

    filename = audio.filename or "recording.webm"

    extension = os.path.splitext(filename)[1]

    if not extension:
        extension = ".webm"

    suffix = extension


    with tempfile.NamedTemporaryFile(
        delete=False,
        suffix=suffix
    ) as temp_file:

        audio.save(temp_file.name)
        temp_path = temp_file.name

    try:

        with open(temp_path, "rb") as audio_file:

            transcription = client.audio.transcriptions.create(
                model="gpt-4o-mini-transcribe",
                file=audio_file
            )

        return jsonify({
            "text": transcription.text
        })

    finally:

        os.remove(temp_path)

@app.route("/realtime-session", methods=["GET", "POST"])
def realtime_session():
    if app.config["DEMO_ENABLED"]:
        return demo_credential()
    if request.method != "GET":
        return jsonify(error="Use GET outside demo mode."), 405

    session_config = {
        "session": {
            "type": "realtime",
            "model": "gpt-realtime-2.1-mini",
            "audio": {
                "output": {
                    "voice": "marin"
                }
            }
        }
    }

    response = requests.post(
        "https://api.openai.com/v1/realtime/client_secrets",
        headers={
            "Authorization": f"Bearer {os.getenv('OPENAI_API_KEY')}",
            "Content-Type": "application/json"
        },
        json=session_config
    )

    return jsonify(response.json()), response.status_code


@app.route("/realtime-tool", methods=["POST"])
def realtime_tool():
    if app.config["DEMO_ENABLED"]:
        return demo_tool()

    if "session_id" not in session:
        session["session_id"] = str(uuid.uuid4())

    data = request.get_json()

    if not data:
        return jsonify({
            "error": "Tool request is required."
        }), 400

    tool_name = data.get("name")
    arguments = data.get("arguments", {})

    if not tool_name:
        return jsonify({
            "error": "Tool name is required."
        }), 400

    restaurant_id = session.get(
        "restaurant_id",
        "sanis"
    )

    session_id = session["session_id"]

    try:

        result = execute_tool(
            tool_name,
            arguments,
            restaurant_id=restaurant_id,
            session_id=session_id
        )

        return jsonify({
            "result": result
        })

    except Exception as error:

        print(
            "Realtime tool error:",
            error
        )

        return jsonify({
            "error": str(error)
        }), 500


@app.route("/admin/orders-data", methods=["GET"])
def admin_orders_data():
    if app.config["DEMO_ENABLED"]:
        return jsonify(error="Unavailable in demo mode."), 404

    orders = get_all_orders()

    return jsonify({
        "orders": orders
    })

@app.route("/admin/orders", methods=["GET"])
def admin_orders():
    if app.config["DEMO_ENABLED"]:
        return jsonify(error="Unavailable in demo mode."), 404

    orders = get_all_orders()

    return render_template(
        "orders.html",
        orders=orders
    )



@app.route(
    "/admin/orders/<int:order_id>/status",
    methods=["POST"]
)
def update_admin_order_status(order_id):
    if app.config["DEMO_ENABLED"]:
        return jsonify(error="Unavailable in demo mode."), 404

    data = request.get_json()

    if not data or "status" not in data:
        return jsonify({
            "error": "Status is required."
        }), 400

    result = update_order_status(
        order_id,
        data["status"]
    )

    return jsonify(result)


if __name__ == "__main__":
    from orderai.services.production_config import initialise_storage
    initialise_storage(app)
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5000")), debug=False)
