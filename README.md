# FastAPI Backend

A FastAPI backend with SQLAlchemy, Alembic migrations, and JWT authentication.

## Structure

```
backend/
├── app/
│   ├── api/
│   │   └── v1/
│   │       ├── endpoints/
│   │       │   ├── auth.py       # Login endpoints
│   │       │   └── users.py      # User CRUD endpoints
│   │       ├── deps.py           # Dependency injection
│   │       └── router.py         # API router
│   ├── core/
│   │   ├── config.py             # App settings (pydantic-settings)
│   │   ├── database.py           # SQLAlchemy engine & session
│   │   └── security.py           # JWT & password hashing
│   ├── models/
│   │   └── user.py               # SQLAlchemy ORM models
│   ├── schemas/
│   │   ├── auth.py               # Auth request/response schemas
│   │   └── user.py               # User Pydantic schemas
│   └── main.py                   # FastAPI app entry point
├── alembic/                      # Database migrations
├── alembic.ini
├── requirements.txt
└── .env.example
```

## Setup

### 1. Create virtual environment

```bash
python -m venv venv
# Windows
venv\Scripts\activate
# macOS/Linux
source venv/bin/activate
```

### 2. Install dependencies

```bash
pip install -r requirements.txt
```

### 3. Configure environment

```bash
cp .env.example .env
# Edit .env with your settings
```

### 4. Run database migrations

```bash
alembic revision --autogenerate -m "initial"
alembic upgrade head
```

### 5. Start the server

```bash
uvicorn app.main:app --reload --port 8000
```

## API Docs

- Swagger UI: http://localhost:8000/docs
- ReDoc: http://localhost:8000/redoc

## Endpoints

| Method | Path | Description | Auth |
|--------|------|-------------|------|
| POST | `/api/v1/auth/login` | Login (JSON) | No |
| POST | `/api/v1/auth/login/token` | Login (form, for Swagger) | No |
| GET | `/api/v1/users/me` | Get current user | Yes |
| PATCH | `/api/v1/users/me` | Update current user | Yes |
| GET | `/api/v1/users/` | List all users | Superuser |
| POST | `/api/v1/users/` | Create user | Superuser |
| GET | `/api/v1/users/{id}` | Get user by ID | Superuser |
| PATCH | `/api/v1/users/{id}` | Update user by ID | Superuser |
| DELETE | `/api/v1/users/{id}` | Delete user | Superuser |
