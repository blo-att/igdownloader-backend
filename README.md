# Deploy (Render or Railway)
1. Put these 4 files in a GitHub repo (app.py, requirements.txt, Procfile, README.md).
2. Render: New > Web Service > pick the repo. Build: `pip install -r requirements.txt`. Start: `gunicorn app:app --timeout 60 --workers 1 --threads 4`.
   Railway: New Project > Deploy from GitHub repo (it reads the Procfile).
3. Copy your service address, for example https://igdl.onrender.com
4. In index.html set:  var API_ENDPOINT="https://igdl.onrender.com/api/download";
5. Test: https://igdl.onrender.com/health should show {"ok":true}
6. Optional: set ALLOWED_ORIGINS to your site address.
If Instagram blocks the server, export your browser cookies to cookies.txt (Netscape format) and add it beside app.py.
