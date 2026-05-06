"""
CARSHOT – GPT-4o Automation
============================
Single (€4,90):  1 Foto  → Showroom → direkter PNG-Download
Bundle (€19,90): 5 Fotos (verschiedene Winkel vom selben Auto)
                 → alle im gleichen Showroom → ZIP-Download

Setup:
  pip install openai flask pillow python-dotenv

.env Datei erstellen:
  OPENAI_API_KEY=sk-...

Starten:
  python carshot_gpt.py
  Dann: http://localhost:5000
"""

import os, io, uuid, base64, zipfile
from pathlib import Path
from flask import Flask, request, jsonify, send_file, render_template_string
from openai import OpenAI
from PIL import Image
from dotenv import load_dotenv

load_dotenv()
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

app = Flask(__name__)
JOBS_DIR = Path("jobs")
JOBS_DIR.mkdir(exist_ok=True)

jobs: dict = {}

# ── PROMPTS ───────────────────────────────────────────────────────────────────

BASE_SHOWROOM = """
You are a professional automotive photo retoucher.

Your task with this car photo:
1. Keep the car EXACTLY as-is – same color, reflections, body shape, wheels, 
   badges, every single detail unchanged on the car itself
2. Remove everything that is NOT the car (road, wall, parking lot, sky, background)
3. Place the car in this exact showroom environment:
   - Polished light grey concrete floor with subtle wet-look reflections
   - Floor-to-ceiling glass windows in background, softly blurred green trees visible outside
   - Soft diffused overhead LED lighting, warm accent lights from the sides
   - Minimalist modern white interior walls
   - Realistic floor reflection of the car directly beneath it
4. The car's existing highlights and reflections should blend naturally with showroom lighting
5. Final result: photorealistic, professional automotive press photo quality

No people, no other vehicles, no text, no logos in background.
"""

# Für Bundle: betont gleichen Showroom für alle Winkel
BUNDLE_EXTRA = """
IMPORTANT for this photo set: This is one of several photos of the SAME car.
All photos must show the EXACT SAME showroom environment:
- Same floor color and reflection style
- Same window position and light coming through
- Same wall color
- Same overall lighting atmosphere
The showroom must look identical across all photos so they form a consistent professional set.
Just adapt the camera perspective to match this particular photo angle.
"""


# ── HILFSFUNKTIONEN ───────────────────────────────────────────────────────────

def prepare_image(file_bytes: bytes) -> io.BytesIO:
    """Skaliert Bild auf max 1024x1024 und gibt PNG-Buffer zurück."""
    img = Image.open(io.BytesIO(file_bytes)).convert("RGB")
    img.thumbnail((1024, 1024), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return buf


def call_gpt(image_buf: io.BytesIO, prompt: str) -> bytes:
    import base64 as b64lib

    # Schritt 1: GPT-4o analysiert das Auto
    image_buf.seek(0)
    img_b64 = b64lib.b64encode(image_buf.read()).decode()

    vision_response = client.chat.completions.create(
        model="gpt-4o",
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {"url": "data:image/png;base64," + img_b64}
                    },
                    {
                        "type": "text",
                        "text": "Describe this car in detail: exact make, model, year, paint color and finish, body style, wheels, badges, camera angle. Only describe the car, not the background. Be specific."
                    }
                ]
            }
        ],
        max_tokens=400
    )
    car_description = vision_response.choices[0].message.content

    # Schritt 2: DALL-E 3 generiert Showroom-Bild
    dalle_prompt = (
        "Professional automotive dealership showroom photograph. "
        "Car: " + car_description + " "
        "Setting: Premium showroom with polished light grey concrete floor with subtle wet reflections, "
        "floor-to-ceiling glass windows showing softly blurred green trees outside, "
        "soft overhead LED spotlights, warm accent lighting from sides, "
        "minimalist modern white interior. Realistic floor reflection of the car. "
        "Style: Professional automotive press photo, photorealistic, 8K. "
        "No people, no other vehicles, no text."
    )

    response = client.images.generate(
        model="dall-e-3",
        prompt=dalle_prompt,
        n=1,
        size="1024x1024",
        quality="hd",
        response_format="b64_json"
    )

    return b64lib.b64decode(response.data[0].b64_json)


def index():
    return send_file("carshot_final.html")

@app.route("/process", methods=["POST"])
def process():
    """
    POST /process
    Form-Data:
      type:   "single" oder "bundle"
      images: ein oder mehrere Bilder

    Response:
      { "download_url": "/download/<job_id>", "status": "done" }
    """
    order_type  = request.form.get("type", "single")
    files       = request.files.getlist("images")

    if not files or not files[0].filename:
        return jsonify({"error": "Kein Bild hochgeladen"}), 400

    if order_type == "bundle" and len(files) < 2:
        return jsonify({"error": "Bundle braucht mindestens 2 Fotos (verschiedene Winkel)"}), 400

    job_id  = uuid.uuid4().hex[:12]
    job_dir = JOBS_DIR / job_id
    job_dir.mkdir()

    images_bytes = [f.read() for f in files]

    try:
        result_files = []

        if order_type == "single":
            # 1 Foto → Showroom → PNG
            buf    = prepare_image(images_bytes[0])
            result = call_gpt(buf, BASE_SHOWROOM)
            out    = job_dir / "carshot.png"
            out.write_bytes(result)
            result_files.append(out)
            jobs[job_id] = {"status": "done", "file": "carshot.png"}

        else:
            # Bundle: jedes Foto (anderer Winkel) → gleicher Showroom → ZIP
            prompt = BASE_SHOWROOM + BUNDLE_EXTRA
            for i, img_bytes in enumerate(images_bytes, 1):
                buf    = prepare_image(img_bytes)
                result = call_gpt(buf, prompt)
                out    = job_dir / f"carshot_{i:02d}.png"
                out.write_bytes(result)
                result_files.append(out)

            # ZIP erstellen
            zip_path = job_dir / "carshot_bundle.zip"
            with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
                for f in result_files:
                    zf.write(f, f.name)

            jobs[job_id] = {"status": "done", "file": "carshot_bundle.zip"}

    except Exception as e:
        return jsonify({"error": f"Verarbeitungsfehler: {str(e)}"}), 500

    return jsonify({
        "job_id":       job_id,
        "download_url": f"/download/{job_id}",
        "status":       "done",
    })


@app.route("/download/<job_id>")
def download(job_id):
    """Gibt fertiges Bild oder ZIP direkt zum Download."""
    job = jobs.get(job_id)
    if not job or job.get("status") != "done":
        return "Nicht gefunden", 404

    filepath = JOBS_DIR / job_id / job["file"]
    if not filepath.exists():
        return "Datei fehlt", 404

    is_zip = filepath.suffix == ".zip"
    return send_file(
        filepath,
        as_attachment=True,
        download_name=filepath.name,
        mimetype="application/zip" if is_zip else "image/png",
    )


# ── START ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    print(f"""
╔══════════════════════════════════════════╗
║  🚗 CARSHOT                             ║
║  http://localhost:{port}                   ║
╠══════════════════════════════════════════╣
║  Single: 1 Foto  → PNG Download         ║
║  Bundle: 5 Fotos → ZIP Download         ║
╚══════════════════════════════════════════╝
    """)
    app.run(host="0.0.0.0", port=port, debug=False)
