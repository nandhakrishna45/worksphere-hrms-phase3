# WorkSphere HRMS — Phase 3

Phase 3 starter implementation for automation, insights, and smart HR features.

## Included modules
- Module 14: Shift management and attendance check-in/out with late/early detection
- Module 15: Leave calendar and team availability
- Module 16: Employee document metadata and secure local upload/download
- Module 17: Performance reviews
- Module 18: Analytics and CSV reports
- Module 19: In-app notifications and alert generation
- Module 20: Employee search and filters

## Stack
- FastAPI
- SQLAlchemy 2
- SQLite by default (PostgreSQL supported through `DATABASE_URL`)
- JWT authentication
- Pydantic validation
- Local document storage in `uploads/`

> Integration note: This is a standalone Phase 3 baseline. Merge the models, routers, and auth dependencies into the existing Phase 1/2 codebase before deployment. Existing Users/Employees/Attendance/Leaves/Payroll schemas may differ, so validate migrations and relationships before using production data.

## Run locally
```bash
python -m venv .venv
# Windows: .venv\\Scripts\\activate
# macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
copy .env.example .env  # Windows
# cp .env.example .env  # macOS/Linux
uvicorn app.main:app --reload
```
Open `http://127.0.0.1:8000/docs`.

## Bootstrap an admin
Set `BOOTSTRAP_ADMIN_EMAIL` and `BOOTSTRAP_ADMIN_PASSWORD` in `.env`, then start the app. On first startup, an admin is created if no user exists with that email.

## Important production work
- Replace local uploads with private object storage and malware scanning.
- Use Alembic migrations; do not rely on `create_all` in production.
- Set a strong `JWT_SECRET_KEY`, enforce HTTPS, rotate secrets, and configure CORS.
- Configure a real scheduler/worker for shift-based automatic attendance and notifications.
- Integrate email/SMS/web-push providers as required.
- Add tests, audit logging, backups, retention policies, and rate limiting.
