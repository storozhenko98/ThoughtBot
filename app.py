from fastapi import FastAPI, Request, BackgroundTasks, Form
from fastapi.responses import HTMLResponse, StreamingResponse
from openai import OpenAI
import sqlite3
import time
import os
import io
import pdfkit
import markdown
import base64

# Init the DB #
conn = sqlite3.connect("db.sqlite3")
cursor = conn.cursor()
cursor.execute("CREATE TABLE IF NOT EXISTS queries (id INTEGER PRIMARY KEY AUTOINCREMENT, result TEXT, username TEXT, passphrase TEXT, audio_file TEXT, plain_text TEXT, status TEXT)")
cursor.execute("CREATE TABLE IF NOT EXISTS api_keys (id INTEGER PRIMARY KEY AUTOINCREMENT, key TEXT)")
conn.commit()
conn.close()
#################

# Fetch OpenAI API key from DB
def get_openai_api_key():
    conn = sqlite3.connect("db.sqlite3")
    cursor = conn.cursor()
    cursor.execute("SELECT key FROM api_keys ORDER BY id DESC LIMIT 1")
    key = cursor.fetchone()
    conn.close()
    return key[0] if key else None

# Initialize FastAPI
app = FastAPI()

# Constants #
sysprompt = "You are a helpful assistant. Format the transcribed notes into a well-ordered format, including sections, bullet points, and any necessary headers."
reg_prompt = "What is the capital of the United States?"
temp_audio_location_dir = "temp_audio_files/"
html_files_path = "html_files/"

########## Speech to Text ##########
def get_text_from_audio(audio_file, api_key):
    audio_file = open(audio_file, "rb")
    client = OpenAI(api_key=api_key)
    transcription = client.audio.transcriptions.create(
        model="whisper-1",
        file=audio_file
    )
    return transcription.text
####################################
########### Text to Well Formatted Text ##########

def get_well_formatted_text(text, api_key):
    client = OpenAI(api_key=api_key)
    response = client.chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": sysprompt},
            {"role": "user", "content": f"Please format the following text: {text}"},
        ],
    )
    return response.choices[0].message.content
####################################################
## Process the query ##
def process_query(username, passphrase, audio_file):
    api_key = get_openai_api_key()
    if not api_key:
        raise ValueError("API key not set")
    
    # Save the base64 of the audio file to the disk
    time_stamp = str(time.time())
    audio_file_path = temp_audio_location_dir + "_" + username + "_" + time_stamp + ".wav"
    with open(audio_file_path, "wb") as f:
        audio_data = base64.b64decode(audio_file)
        f.write(audio_data)
    # Get into DB 
    conn = sqlite3.connect("db.sqlite3")
    cursor = conn.cursor()
    cursor.execute("INSERT INTO queries (username, passphrase, audio_file, status) VALUES (?, ?, ?, ?)", (username, passphrase, audio_file_path, "processing"))
    query_id = cursor.lastrowid
    conn.commit()
    conn.close()
    # Get the text from the audio file
    text = get_text_from_audio(audio_file_path, api_key)
    # Remove the audio file
    os.remove(audio_file_path)
    # Update the DB to insert the plain text based on user name and id 
    conn = sqlite3.connect("db.sqlite3")
    cursor = conn.cursor()
    cursor.execute("UPDATE queries SET plain_text = ? WHERE id = ?", (text, query_id))
    conn.commit()
    conn.close()
     # Get the well formatted text
    well_formatted_text = get_well_formatted_text(text, api_key)
    # Save the query to the DB
    conn = sqlite3.connect("db.sqlite3")
    cursor = conn.cursor()
    cursor.execute("UPDATE queries SET result = ?, status = ? WHERE id = ?", (well_formatted_text, "completed", query_id))
    conn.commit()
    conn.close()

# New function to convert Markdown to PDF
def convert_markdown_to_pdf(markdown_text):
    html_text = markdown.markdown(markdown_text)
    pdf = pdfkit.from_string(html_text, False)
    return pdf

@app.get("/")
def return_homepage():
    html = open(html_files_path + "index.html", "r").read()
    return HTMLResponse(content=html, status_code=200)

@app.post("/query")
async def query(request: Request, background_tasks: BackgroundTasks):
    req = await request.json()
    username = req["username"]
    passphrase = req["passphrase"]
    audio_file = req["audio_file"]
    background_tasks.add_task(process_query, username, passphrase, audio_file)
    return {"message": "Query is being processed in the background"}

# Retrieve Page
@app.get("/retrieve")
def send_retrieve_page():
    html = open(html_files_path + "retrieve.html", "r").read()
    return HTMLResponse(content=html, status_code=200)

# Retrieve Query
@app.post("/retrieve/by_username")
async def retrieve_by_username(request: Request):
    req = await request.json()
    username = req["username"]
    passphrase = req["passphrase"]
    conn = sqlite3.connect("db.sqlite3")
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM queries WHERE username = ? AND passphrase = ?", (username, passphrase))
    result = cursor.fetchall()
    conn.close()
    if len(result) == 0:
        return {"message": "No queries found"}
    else:
        return {"queries": result}

# New route to download the result as a PDF
@app.get("/download/{query_id}")
def download_result(query_id: int):
    conn = sqlite3.connect("db.sqlite3")
    cursor = conn.cursor()
    cursor.execute("SELECT result FROM queries WHERE id = ?", (query_id,))
    query_result = cursor.fetchone()
    conn.close()

    if query_result:
        markdown_text = query_result[0]
        pdf_data = convert_markdown_to_pdf(markdown_text)
        pdf_stream = io.BytesIO(pdf_data)
        pdf_stream.seek(0)
        return StreamingResponse(pdf_stream, media_type="application/pdf", headers={"Content-Disposition": f"attachment;filename=result_{query_id}.pdf"})
    else:
        return {"message": "Query result not found"}

# Admin Page
@app.get("/admin")
def admin_page():
    html = open(html_files_path + "admin.html", "r").read()
    return HTMLResponse(content=html, status_code=200)

@app.post("/admin/set_key")
async def set_api_key(request: Request):
    form = await request.form()
    passphrase = form.get("passphrase")
    api_key = form.get("api_key")
    if passphrase == "INSERT HERE":
        conn = sqlite3.connect("db.sqlite3")
        cursor = conn.cursor()
        cursor.execute("INSERT INTO api_keys (key) VALUES (?)", (api_key,))
        conn.commit()
        conn.close()
        return {"message": "API key set successfully"}
    else:
        return {"message": "Invalid passphrase"}
