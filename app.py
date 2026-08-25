from flask import Flask, render_template, redirect, send_from_directory
from dotenv import load_dotenv
import json
import os
import requests

load_dotenv()

app = Flask(__name__)

REAL_DEBRID_TOKEN = os.getenv("REAL_DEBRID_TOKEN")
REAL_DEBRID_CUSTOM_ID = os.getenv("REAL_DEBRID_CUSTOM_ID")
SMB_FOLDER = os.getenv("SMB_FOLDER")
HOME_URL = os.getenv("HOME_URL")

if not HOME_URL:
    raise RuntimeError("HOME_URL is not configured")

if not REAL_DEBRID_TOKEN:
    raise RuntimeError("REAL_DEBRID_TOKEN is not configured")

if not REAL_DEBRID_CUSTOM_ID:
    raise RuntimeError("REAL_DEBRID_CUSTOM_ID is not configured")

if not SMB_FOLDER:
    raise RuntimeError("SMB_FOLDER is not configured")

debridURL = "https://api.real-debrid.com/rest/1.0/torrents"
debridCustom = (
    f"https://my.real-debrid.com/{REAL_DEBRID_CUSTOM_ID}/torrents/"
)

headers = {
    "Authorization": f"Bearer {REAL_DEBRID_TOKEN}"
}

response = requests.get(
    debridURL,
    headers=headers
)
response.raise_for_status()

data = response.json()
print("Success! Data retrieved:")

debrid_dict = []

for x in range(len(data)):
    links = data[x].get("links", [])

    tempDict = {
        "id": x,
        "filename": data[x].get("filename"),
        "rd_link": links[0] if links else "",
        "torrent_id": data[x].get("id"),
        "files": data[x].get("files", [])
    }

    debrid_dict.append(tempDict)

unrestrict = "https://api.real-debrid.com/rest/1.0/unrestrict/link"

headers = {
    "Authorization": f"Bearer {REAL_DEBRID_TOKEN}"
}

@app.route("/debrid/play/<int:id>")
def debrid_play(id):
    if id < 0 or id >= len(debrid_dict):
        return "Video not found", 404

    rd_link = debrid_dict[id]["rd_link"]

    response = requests.post(
        unrestrict,
        headers=headers,
        data={
            "link": rd_link,
            "remote": 0
        }
    )

    if response.status_code != 200:
        return response.text, response.status_code

    unrestricted = response.json()["download"]

    return redirect(unrestricted)

@app.route("/")
def home():
    with open("playlist.json", encoding="utf-8") as f:
        videos = json.load(f)

    return render_template("index.html", videos=videos)

@app.route("/debrid")
def debrid():
    return render_template("debrid.html", videos=debrid_dict)

@app.route("/play/<int:id>")
def play(id):
    with open("playlist.json", encoding="utf-8") as f:
        videos = json.load(f)

    for v in videos:
        if v["id"] == id:
            url = v["url"]

            # SMB entries
            if url.startswith(f"{HOME_URL}"):
                filename = url.split("/VR/")[-1]
                return redirect("/smb/" + filename)

            # Real-Debrid entries
            return redirect(url, code=302)

    return "Video not found", 404


@app.route("/smb/<path:filename>")
def smb(filename):
    return send_from_directory(SMB_FOLDER, filename)


@app.route("/bookmark/<int:id>", methods=["POST"])
def bookmark(id):
    if id < 0 or id >= len(debrid_dict):
        return "Video not found", 404

    with open("playlist.json", "r", encoding="utf-8") as f:
        playlist = json.load(f)

    video = debrid_dict[id]

    newURL = debridCustom + video['filename']+ "/" + video['filename']

    # Don't add duplicates
    for item in playlist:
        if item["url"] == video["rd_link"]:
            return redirect("/debrid")

    new_id = max((item["id"] for item in playlist), default=0) + 1

    playlist.append({
        "id": new_id,
        "title": video["filename"],
        "url": newURL,
        "stars": []
    })

    with open("playlist.json", "w", encoding="utf-8") as f:
        json.dump(playlist, f, indent=2)

    return redirect("/debrid")


app.run(host="0.0.0.0", port=8080)