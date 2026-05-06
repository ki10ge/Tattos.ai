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
    """Sendet Bild an GPT-4o Image Edit, gibt Ergebnis-bytes zurück."""
    image_buf.seek(0)
    response = client.images.edit(
        model="gpt-image-1",
        image=image_buf,
        prompt=prompt,
        n=1,
        size="1024x1024",
    )
    item = response.data[0]
    if getattr(item, "b64_json", None):
        return base64.b64decode(item.b64_json)
    import requests
    return requests.get(item.url).content


# ── ROUTEN ────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template_string("""<!DOCTYPE html>
<html lang="de"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Carshot</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:sans-serif;background:#0a0a0a;color:#fafaf8;
     max-width:480px;margin:0 auto;padding:32px 20px}
h1{font-size:32px;margin-bottom:4px}
h1 span{color:#C8FF00}
p{color:#777;margin-bottom:24px;font-size:14px;line-height:1.6}
label{font-size:12px;color:#555;letter-spacing:1px;
      text-transform:uppercase;display:block;margin-bottom:6px}
select,input[type=file]{width:100%;padding:10px;background:#141414;
  border:1px solid #222;color:#fafaf8;border-radius:8px;
  margin-bottom:20px;font-size:14px}
.hint{font-size:12px;color:#444;margin-top:-14px;margin-bottom:20px}
button{width:100%;background:#C8FF00;color:#000;border:none;
       padding:14px;border-radius:10px;font-size:16px;
       font-weight:700;cursor:pointer}
button:disabled{opacity:.5;cursor:not-allowed}
#result{margin-top:24px;padding:20px;background:#141414;
        border-radius:10px;display:none}
#result p{margin:0 0 12px;color:#aaa}
.dl-btn{display:inline-block;background:#C8FF00;color:#000;
        padding:12px 24px;border-radius:8px;text-decoration:none;
        font-weight:700;font-size:15px}
.progress{color:#C8FF00;font-size:14px}
.error{color:#ff4444}
</style></head>
<body>
<h1>car<span>shot</span></h1>
<p>Foto hochladen → KI macht deinen Hintergrund zum Showroom → sofort herunterladen.</p>

<label>Paket wählen</label>
<select id="type" onchange="onTypeChange()">
  <option value="single">1 Bild – €4,90</option>
  <option value="bundle">5 Bilder Bundle – €19,90</option>
</select>

<label id="file-label">Foto hochladen</label>
<input type="file" id="files" accept="image/*">
<p class="hint" id="file-hint">JPG oder PNG · Max. 10 MB</p>

<button id="btn" onclick="upload()">Bild bearbeiten →</button>

<div id="result">
  <p id="result-msg"></p>
  <a id="dl-link" class="dl-btn" style="display:none">
    Herunterladen →
  </a>
</div>

<script>
function onTypeChange() {
  const type = document.getElementById('type').value;
  const inp  = document.getElementById('files');
  const lbl  = document.getElementById('file-label');
  const hint = document.getElementById('file-hint');
  if (type === 'bundle') {
    inp.multiple = true;
    lbl.textContent = '5 Fotos hochladen (verschiedene Winkel)';
    hint.textContent = 'Lade Fotos von vorne, hinten, links, rechts und schräg hoch · je max. 10 MB';
  } else {
    inp.multiple = false;
    lbl.textContent = 'Foto hochladen';
    hint.textContent = 'JPG oder PNG · Max. 10 MB';
  }
}

async function upload() {
  const type  = document.getElementById('type').value;
  const files = document.getElementById('files').files;
  const btn   = document.getElementById('btn');
  const res   = document.getElementById('result');
  const msg   = document.getElementById('result-msg');
  const link  = document.getElementById('dl-link');

  if (!files.length) { alert('Bitte Foto auswählen'); return; }
  if (type === 'bundle' && files.length < 2) {
    alert('Bitte mindestens 2 Fotos für das Bundle hochladen');
    return;
  }

  btn.disabled = true;
  btn.textContent = '⏳ Wird bearbeitet...';
  res.style.display = 'block';
  link.style.display = 'none';
  msg.className = 'progress';
  msg.textContent = type === 'bundle'
    ? `⏳ ${files.length} Fotos werden bearbeitet... (ca. ${files.length * 15} Sekunden)`
    : '⏳ Bild wird bearbeitet... (ca. 15 Sekunden)';

  const fd = new FormData();
  fd.append('type', type);
  for (const f of files) fd.append('images', f);

  try {
    const r    = await fetch('/process', { method: 'POST', body: fd });
    const data = await r.json();

    if (data.error) {
      msg.className = 'error';
      msg.textContent = '❌ ' + data.error;
    } else {
      msg.className = '';
      msg.textContent = '✅ Fertig! Dein Bild ist bereit.';
      link.href = data.download_url;
      link.textContent = type === 'bundle' ? 'ZIP herunterladen →' : 'Bild herunterladen →';
      link.style.display = 'inline-block';
    }
  } catch(e) {
    msg.className = 'error';
    msg.textContent = '❌ Fehler: ' + e.message;
  }

  btn.disabled = false;
  btn.textContent = 'Bild bearbeiten →';
}
</script>
</body></html>""")


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
