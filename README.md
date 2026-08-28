# Backend Learning

A small FastAPI project for backend practice.

## Run locally

```powershell
cd backend_learning
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn app.main:app --reload
```

Open `http://127.0.0.1:8000/docs` to view the API documentation.

## Project layout

- `app/main.py`: FastAPI application entry point
- `app/routers/`: API routes
- `app/services/`: Business logic
- `app/models/`: Data models
- `app/schemas/`: Request and response schemas
- `app/utils/`: Shared utilities
- `app/core/`: Configuration and infrastructure
- `tests/`: Automated tests
- `data/`: Local data files
- `logs/`: Application logs
- My Python backend learning project.
