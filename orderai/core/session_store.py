sessions = {}


def get_session_state(session_id):

    print("SESSION STORE ID:", session_id)

    if session_id is None:
        session_id = "default"

    if session_id not in sessions:
        sessions[session_id] = {
            "order": {
                "items": [],
                "status": "building",
                "validated_turn": None
            },
            "pending_action": {
                "action": None,
                "quantity": None,
                "search_term": None
            }
        }

    return sessions[session_id]