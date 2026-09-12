# Backend Learning

A small FastAPI project for backend practice.

## Run locally

```powershell
cd backend_learning
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt
uvicorn app.main:app --reload
```

Open `http://127.0.0.1:8000/docs` to view the API documentation.

## Quality checks

```powershell
ruff check .
ruff format --check .
pytest --cov=app
```

## Project layout

- `app/main.py`: FastAPI application entry point
- `app/routers/`: API routes
- `app/services/`: Business logic
- `app/models/`: Data models
- `app/schemas/`: Request and response schemas
- `app/core/`: Configuration and infrastructure
- `tests/`: Automated tests
- `examples/`: Standalone database and Redis learning scripts
