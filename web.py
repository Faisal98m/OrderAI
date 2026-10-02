import uuid
import os
import tempfile

from openai import OpenAI

from flask import Flask, request, jsonify, render_template, session
from agent import run_agent

client = OpenAI(
    api_key=os.getenv("OPENAI_API_KEY")
)


app = Flask(__name__)

app.secret_key = "dev-secret-key"



@app.route("/")
def home():
    return render_template("index.html")



@app.route("/chat", methods=["POST"])
def chat():

    if "session_id" not in session:
        session["session_id"] = str(uuid.uuid4())

    data = request.get_json()

    if not data or "message" not in data:
        return jsonify({
            "error": "A message is required."
        }), 400

    message = data["message"]

    previous_response_id = session.get("previous_response_id")

    # Each customer message is a new turn
    turn_id = session.get("turn_id", 0) + 1
    session["turn_id"] = turn_id

    response, new_response_id = run_agent(
        message,
        previous_response_id,
        turn_id
    )

    session["previous_response_id"] = new_response_id

    return jsonify({
        "message": response
    })


@app.route("/transcribe", methods=["POST"])
def transcribe():

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

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)