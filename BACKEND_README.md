# Zeefi Clothing

## Setup

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
python app.py
```

Open http://localhost:5000. Set a strong `JWT_SECRET`, `ADMIN_EMAIL`, and `ADMIN_PASSWORD` before the first run. The SQLite database is created automatically at `DATABASE_PATH`.

## Admin

Create or update the administrator using `ADMIN_EMAIL` and `ADMIN_PASSWORD`, then sign in at `login.html`. The role is enforced again by every `/api/admin/*` endpoint.
