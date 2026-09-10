"""
AURA - main Flask application.
Run with: python3 app.py
"""
from flask import Flask, request, jsonify, render_template

import config
from core import db, orchestrator, llm
from agents import coding_agent
from github import self_improve

app = Flask(__name__)
db.init_db()


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/status")
def status():
    return jsonify({
        "llm_available": llm.is_available(),
        "model": config.OLLAMA_MODEL,
        "autonomy_mode": config.AUTONOMY_MODE,
    })


@app.route("/api/chat", methods=["POST"])
def chat():
    data = request.get_json(force=True)
    message = data.get("message", "").strip()
    if not message:
        return jsonify({"error": "message is required"}), 400
    result = orchestrator.handle_message(message)
    return jsonify(result)


@app.route("/api/tasks", methods=["GET"])
def get_tasks():
    return jsonify(db.list_tasks())


@app.route("/api/tasks/<int:task_id>", methods=["GET"])
def get_task(task_id):
    task = db.get_task(task_id)
    if not task:
        return jsonify({"error": "not found"}), 404
    task["steps"] = db.get_task_steps(task_id)
    return jsonify(task)


@app.route("/api/approvals", methods=["GET"])
def get_approvals():
    return jsonify(db.list_pending_approvals())


@app.route("/api/approvals/<int:approval_id>", methods=["POST"])
def resolve_approval(approval_id):
    data = request.get_json(force=True)
    approved = bool(data.get("approved"))
    db.resolve_approval(approval_id, approved)

    approval = db.get_approval(approval_id)
    result = None
    if approved and approval["action"] == "delete_file":
        try:
            result = coding_agent.execute_approved_delete(approval_id)
        except PermissionError as e:
            return jsonify({"error": str(e)}), 400

    db.log_activity(
        "approval_resolved",
        f"{approval['action']}: {'approved' if approved else 'rejected'}",
        approval["task_id"],
    )
    return jsonify({"approval_id": approval_id, "approved": approved, "result": result})


@app.route("/api/memory/search", methods=["GET"])
def memory_search():
    query = request.args.get("q", "")
    kind = request.args.get("kind")
    if not query:
        return jsonify(db.search_memory(kind=kind))
    from core.embeddings import embed
    emb = embed(query)
    return jsonify(db.search_memory(query_embedding=emb, kind=kind))


@app.route("/api/activity", methods=["GET"])
def get_activity():
    return jsonify(db.get_activity())


@app.route("/api/workspace/files", methods=["GET"])
def workspace_files():
    from tools import file_tools
    return jsonify(file_tools.list_files())


@app.route("/api/workspace/file", methods=["GET"])
def workspace_file_content():
    from tools import file_tools
    path = request.args.get("path", "")
    try:
        return jsonify({"path": path, "content": file_tools.read_file(path)})
    except Exception as e:
        return jsonify({"error": str(e)}), 400


@app.route("/api/proposals", methods=["GET"])
def get_proposals():
    """Self-improvement proposals: branches AURA has pushed + their PR status.
    Merging is never done from here — follow the pr_url to GitHub to review
    and merge yourself."""
    return jsonify(db.list_proposals())


@app.route("/api/github/status", methods=["GET"])
def github_status():
    return jsonify({
        "configured": self_improve.is_configured(),
        "repo": config.GITHUB_REPO or None,
        "base_branch": config.GITHUB_BASE_BRANCH,
    })


if __name__ == "__main__":
    print(f"AURA starting on http://{config.FLASK_HOST}:{config.FLASK_PORT}")
    print(f"LLM: Ollama @ {config.OLLAMA_HOST}, model={config.OLLAMA_MODEL}")
    if not llm.is_available():
        print("WARNING: Ollama is not reachable. Chat/research/coding agents need it running.")
        print("  Install:  pkg install ollama   (or see https://ollama.com)")
        print(f"  Then:     ollama serve &  &&  ollama pull {config.OLLAMA_MODEL}")
    app.run(host=config.FLASK_HOST, port=config.FLASK_PORT, debug=config.FLASK_DEBUG)
