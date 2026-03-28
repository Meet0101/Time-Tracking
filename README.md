# TimeTrack (Django internship project)

Full-stack **Django** app: HTML templates + CSS/JS (**frontend**) and Python models/views/API (**backend**) live in **one repository** (normal for server-rendered Django).

## Google Form — two GitHub links (same repo, different folders)

Replace `YOUR_GITHUB_USERNAME` with your GitHub username (repo name: `timetracking`).

| Field | Paste this URL |
|--------|----------------|
| **Frontend** | `https://github.com/YOUR_GITHUB_USERNAME/timetracking/tree/main/timetracking/templates` |
| **Backend** | `https://github.com/YOUR_GITHUB_USERNAME/timetracking/tree/main/timetracking/core` |

Optional extra frontend assets: `.../tree/main/timetracking/static` (CSS, JS, images).

Main repo: `https://github.com/YOUR_GITHUB_USERNAME/timetracking`

## Run locally

1. Python 3.11+ recommended, PostgreSQL running.  
2. `cd timetracking`  
3. `python -m venv venv` → activate → `pip install -r requirements.txt`  
4. Copy `timetracking/.env.example` → `timetracking/.env` and set DB + secrets.  
5. From folder that contains `manage.py`: `python manage.py migrate` then `python manage.py runserver`  
6. Open `http://127.0.0.1:8000/core/`  

Demo data: `python manage.py seed_demo_data --reset`

## Email to faculty (template)

**Subject:** GitHub links — TimeTrack internship project  

**Body:**  
Respected Sir,  

Please find the project repository and the separate links for frontend (templates) and backend (Django app code) as required by the form:  

- Repository: `https://github.com/YOUR_GITHUB_USERNAME/timetracking`  
- Frontend (templates): `https://github.com/YOUR_GITHUB_USERNAME/timetracking/tree/main/timetracking/templates`  
- Backend (`core` app): `https://github.com/YOUR_GITHUB_USERNAME/timetracking/tree/main/timetracking/core`  

This is a Django project; UI and server code are in the same repo under different folders.  

Thank you,  
[Your name]
