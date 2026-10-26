"""Linear connector operations used by the Pace Hub."""
from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request


def _access_token(refresh_token: str) -> str:
    data = urllib.parse.urlencode({"refresh_token": refresh_token, "grant_type": "refresh_token", "client_id": os.environ["PACE_LINEAR_CLIENT_ID"], "client_secret": os.environ["PACE_LINEAR_CLIENT_SECRET"]}).encode()
    request = urllib.request.Request("https://api.linear.app/oauth/token", data=data, headers={"Content-Type": "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(request, timeout=20) as response:
        return json.loads(response.read())["access_token"]


def assigned_issues(refresh_token: str) -> list[dict]:
    query = "{ viewer { assignedIssues(first: 50) { nodes { id identifier title priority dueDate state { name } team { name } } } } }"
    request = urllib.request.Request("https://api.linear.app/graphql", data=json.dumps({"query": query}).encode(), headers={"Authorization": "Bearer " + _access_token(refresh_token), "Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=20) as response:
        nodes = json.loads(response.read())["data"]["viewer"]["assignedIssues"]["nodes"]
    return [{"id": node["id"], "identifier": node["identifier"], "title": node["title"], "priority": node.get("priority"), "due_date": node.get("dueDate"), "state": node.get("state", {}).get("name"), "team": node.get("team", {}).get("name")} for node in nodes]
